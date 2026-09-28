# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import datetime
import json
import os

import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback


# HARDCODED project ID as required to prevent Agent Platform project number resolution issues
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-02-a988f706a444"
BUCKET_NAME = "travel-concierge-images-qwiklabs-gcp-02-a988f706a444"
MEMORY_BANK_ID = "5756855966058414080"


# WRITE: after each turn, send the session to Memory Bank for extraction.
async def generate_memories_callback(callback_context: CallbackContext):
    await callback_context.add_session_to_memory()
    return None


def memory_bank_service_builder():
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


# Configure AgentEngineSandboxCodeExecutor from deployment_metadata.json
DEPLOYMENT_METADATA_PATH = Path(__file__).parent.parent / "deployment_metadata.json"
sandbox_executor = None

if DEPLOYMENT_METADATA_PATH.exists():
    try:
        with open(DEPLOYMENT_METADATA_PATH, "r") as f:
            metadata = json.load(f)
        sandbox_resource = metadata.get("sandbox_resource_name")
        engine_resource = metadata.get("remote_agent_runtime_id")
        if sandbox_resource:
            sandbox_executor = AgentEngineSandboxCodeExecutor(sandbox_resource_name=sandbox_resource)
        elif engine_resource:
            sandbox_executor = AgentEngineSandboxCodeExecutor(agent_engine_resource_name=engine_resource)
    except Exception:
        pass




def geocode_address(address: str) -> dict:
    """Converts a street address or location name into geographic coordinates (latitude and longitude).

    Args:
        address: The location address string (e.g., '1600 Amphitheatre Pkwy, Mountain View, CA' or 'Eiffel Tower, Paris').

    Returns:
        A dictionary containing formatted address, latitude, and longitude.
    """
    maps_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not maps_key or maps_key == "PASTE_KEY_HERE":
        return {"error": "GOOGLE_MAPS_API_KEY environment variable is not configured with a valid key."}

    encoded_address = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={maps_key}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TravelConcierge/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") == "OK" and data.get("results"):
                res = data["results"][0]
                loc = res.get("geometry", {}).get("location", {})
                return {
                    "formatted_address": res.get("formatted_address"),
                    "latitude": loc.get("lat"),
                    "longitude": loc.get("lng"),
                    "location_type": res.get("geometry", {}).get("location_type"),
                }
            return {"error": f"Geocoding failed: {data.get('status')}"}
    except Exception as e:
        return {"error": f"Failed to geocode address: {str(e)}"}


def find_nearby_places(latitude: float, longitude: float, place_type: str = "tourist_attraction", radius_meters: float = 5000.0) -> dict:
    """Finds nearby places of interest around given geographic coordinates using the Places API (New).

    Args:
        latitude: Center latitude coordinate.
        longitude: Center longitude coordinate.
        place_type: Type of place to search for (e.g., 'tourist_attraction', 'restaurant', 'museum', 'lodging').
        radius_meters: Search radius in meters (default 5000 meters).

    Returns:
        A dictionary containing a list of nearby places with name, address, location, and rating.
    """
    maps_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not maps_key or maps_key == "PASTE_KEY_HERE":
        return {"error": "GOOGLE_MAPS_API_KEY environment variable is not configured with a valid key."}

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": maps_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types,places.rating",
    }
    payload = {
        "includedTypes": [place_type],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                },
                "radius": float(radius_meters),
            }
        },
    }

    try:
        req_body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            places_list = []
            for item in data.get("places", []):
                places_list.append({
                    "name": item.get("displayName", {}).get("text"),
                    "address": item.get("formattedAddress"),
                    "location": item.get("location"),
                    "rating": item.get("rating"),
                    "types": item.get("types", []),
                })
            return {
                "center": {"latitude": latitude, "longitude": longitude},
                "place_type": place_type,
                "places": places_list,
            }
    except Exception as e:
        return {"error": f"Failed to search nearby places: {str(e)}"}


def get_currency_exchange_rates(base_currency: str = "USD", target_currencies: str = "EUR,JPY,GBP,AUD,CAD") -> dict:
    """Fetches real-time foreign exchange rates for travel planning.

    Args:
        base_currency: The 3-letter currency code to convert from (default 'USD').
        target_currencies: Optional comma-separated list of target currency codes (e.g., 'EUR,JPY,GBP').

    Returns:
        A dictionary containing base currency, date, and live conversion rates.
    """
    base = base_currency.upper().strip()
    api_key = os.getenv("CURRENCY_API_KEY", "")
    
    url = f"https://api.frankfurter.app/latest?from={base}"
    if target_currencies:
        symbols = ",".join([c.strip().upper() for c in target_currencies.split(",")])
        url += f"&to={symbols}"

    try:
        headers = {"User-Agent": "TravelConcierge/1.0"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return {
                "base": data.get("base", base),
                "date": data.get("date"),
                "rates": data.get("rates", {}),
            }
    except Exception as e:
        return {"error": f"Failed to fetch live exchange rates for {base}: {str(e)}"}


def generate_destination_image(
    destination_name: str,
    prompt: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Generates a travel destination image using gemini-3.1-flash-lite-image in global region.

    Saves the image as an artifact in Playground and uploads it to Cloud Storage.

    Args:
        destination_name: Name of the destination (e.g., 'Paris', 'Tokyo', 'Bali').
        prompt: Optional specific image prompt or description.
        tool_context: Context provided automatically by ADK runtime for artifact saving.

    Returns:
        A dictionary containing destination_name, prompt, and public image_url.
    """
    description = prompt if prompt else f"A breathtaking high quality travel photo of {destination_name}"

    try:
        genai_client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")
        resp = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=description,
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )

        image_bytes = None
        mime_type = "image/jpeg"
        if resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts:
            for part in resp.candidates[0].content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    mime_type = part.inline_data.mime_type or "image/jpeg"
                    break

        if image_bytes:
            filename = f"{destination_name.lower().replace(' ', '_')}_{uuid.uuid4().hex[:6]}.jpg"

            # 1. Save artifact to Playground's Artifacts panel via ToolContext
            if tool_context is not None:
                artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                tool_context.save_artifact(filename=filename, artifact=artifact_part)

            # 2. Upload in-memory image bytes to public Cloud Storage bucket
            storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
            blob = storage_client.bucket(BUCKET_NAME).blob(filename)
            blob.upload_from_string(image_bytes, content_type=mime_type)

            public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
            return {
                "destination_name": destination_name,
                "prompt": description,
                "image_url": public_url,
            }
        return {"error": f"No image data returned from model for {destination_name}."}
    except Exception as e:
        return {"error": f"Failed to generate destination image: {str(e)}"}


def generate_destination_video(
    destination_name: str,
    prompt: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Generates a short travel preview video for a destination using gemini-omni-flash-preview in global region.

    Saves the video as an artifact in Playground and uploads it to Cloud Storage.

    Args:
        destination_name: Name of the travel destination or item (e.g., 'Paris', 'Tokyo', 'Bali').
        prompt: Optional specific video prompt description.
        tool_context: Context provided automatically by ADK runtime for artifact saving.

    Returns:
        A dictionary containing destination_name, prompt, and public video_url.
    """
    description = prompt if prompt else f"A scenic cinematic short video of {destination_name}"

    try:
        genai_client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")
        interaction = genai_client.interactions.create(
            model="gemini-omni-flash-preview",
            input=description,
        )

        video_bytes = None
        mime_type = "video/mp4"

        if hasattr(interaction, "output_video") and interaction.output_video and getattr(interaction.output_video, "data", None):
            raw_data = interaction.output_video.data
            if isinstance(raw_data, str):
                video_bytes = base64.b64decode(raw_data)
            elif isinstance(raw_data, bytes):
                video_bytes = raw_data
        elif hasattr(interaction, "steps"):
            for step in getattr(interaction, "steps", []):
                if getattr(step, "type", None) == "model_output":
                    for c in getattr(step, "content", []):
                        if getattr(c, "type", None) == "video" or getattr(c, "mime_type", "").startswith("video"):
                            raw_data = getattr(c, "data", None)
                            if raw_data:
                                video_bytes = base64.b64decode(raw_data) if isinstance(raw_data, str) else raw_data
                                break

        if video_bytes:
            filename = f"{destination_name.lower().replace(' ', '_')}_{uuid.uuid4().hex[:6]}.mp4"

            # 1. Save artifact to Playground's Artifacts panel via ToolContext
            if tool_context is not None:
                artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
                tool_context.save_artifact(filename=filename, artifact=artifact_part)

            # 2. Upload in-memory video bytes to public Cloud Storage bucket
            storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
            blob = storage_client.bucket(BUCKET_NAME).blob(filename)
            blob.upload_from_string(video_bytes, content_type=mime_type)

            public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
            return {
                "destination_name": destination_name,
                "prompt": description,
                "video_url": public_url,
            }
        return {"error": f"No video data returned from model for {destination_name}."}
    except Exception as e:
        return {"error": f"Failed to generate destination video: {str(e)}"}




def _get_firestore_client() -> firestore.Client:
    return firestore.Client(project=FIRESTORE_PROJECT_ID)


def list_destinations(category: str = "") -> list[dict]:
    """Retrieves destinations from the travel catalog database.

    Args:
        category: Optional category filter (e.g., 'City & Culture', 'Beach & Nature', 'Art & Romance').

    Returns:
        A list of destination dictionaries.
    """
    db = _get_firestore_client()
    ref = db.collection("destinations")
    docs = ref.stream()
    destinations = []
    for doc in docs:
        data = doc.to_dict()
        if category and category.lower() not in data.get("category", "").lower():
            continue
        destinations.append(data)
    return destinations


def get_destination(destination_id: str) -> dict:
    """Retrieves details for a specific destination by ID (e.g. 'paris', 'tokyo', 'bali').

    Args:
        destination_id: The unique ID string of the destination.

    Returns:
        A dictionary containing the destination details.
    """
    db = _get_firestore_client()
    doc = db.collection("destinations").document(destination_id.lower()).get()
    if doc.exists:
        return doc.to_dict()
    return {"error": f"Destination '{destination_id}' not found in database."}


def add_destination(
    id: str,
    name: str,
    country: str,
    category: str,
    description: str,
    avg_cost_per_day: float,
    best_season: str,
) -> str:
    """Adds or updates a destination in the travel catalog database.

    Args:
        id: Unique identifier for the destination (e.g., 'rome').
        name: Display name of the destination (e.g., 'Rome').
        country: Country where the destination is located.
        category: Travel category (e.g., 'Historic & Culture', 'Beach & Nature').
        description: Detailed description of the destination.
        avg_cost_per_day: Estimated average daily cost in USD.
        best_season: Best season to visit (e.g., 'Spring', 'Autumn').

    Returns:
        A confirmation message indicating the destination was saved.
    """
    db = _get_firestore_client()
    doc_id = id.lower().strip()
    data = {
        "id": doc_id,
        "name": name,
        "country": country,
        "category": category,
        "description": description,
        "avg_cost_per_day": float(avg_cost_per_day),
        "best_season": best_season,
    }
    db.collection("destinations").document(doc_id).set(data)
    return f"Destination '{name}' ({doc_id}) successfully saved to Firestore."


def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        query: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are Travel Concierge, a helpful AI travel assistant designed to help users plan trips, "
        "remember user preferences and facts across conversations (including ALL user allergies, dietary restrictions, and health conditions), "
        "execute python code safely in a sandbox for calculations, geocode addresses, find nearby attractions and places, "
        "search destination options from the catalog, fetch live foreign exchange rates, generate destination preview images, and add new destinations."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=a2ui_instruction,
    tools=[
        PreloadMemoryTool(),
        get_weather,
        get_current_time,
        list_destinations,
        get_destination,
        add_destination,
        generate_destination_image,
        generate_destination_video,
        get_currency_exchange_rates,
        geocode_address,
        find_nearby_places,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
    code_executor=sandbox_executor,
)




app = App(
    root_agent=root_agent,
    name="app",
)




