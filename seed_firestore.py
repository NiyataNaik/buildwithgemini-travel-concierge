import os
from google.cloud import firestore

# HARDCODED project ID as required to prevent Agent Platform project number resolution issues
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-02-a988f706a444"

SEED_DESTINATIONS = [
    {
        "id": "tokyo",
        "name": "Tokyo",
        "country": "Japan",
        "category": "City & Culture",
        "description": "A bustling metropolis blending ultra-modern neon skyscrapers with historic temples and world-class culinary experiences.",
        "avg_cost_per_day": 200.0,
        "best_season": "Spring",
    },
    {
        "id": "paris",
        "name": "Paris",
        "country": "France",
        "category": "Art & Romance",
        "description": "The capital of France, famous for the Eiffel Tower, Louvre museum, charming cafes, and high fashion.",
        "avg_cost_per_day": 250.0,
        "best_season": "Autumn",
    },
    {
        "id": "bali",
        "name": "Bali",
        "country": "Indonesia",
        "category": "Beach & Nature",
        "description": "Tropical paradise known for volcanic mountains, iconic rice paddies, beaches, and coral reefs.",
        "avg_cost_per_day": 90.0,
        "best_season": "Summer",
    },
    {
        "id": "kyoto",
        "name": "Kyoto",
        "country": "Japan",
        "category": "Historic & Peaceful",
        "description": "Japan's cultural heart, famous for classical Buddhist temples, gardens, imperial palaces, and traditional wooden houses.",
        "avg_cost_per_day": 180.0,
        "best_season": "Spring",
    },
    {
        "id": "reykjavik",
        "name": "Reykjavik",
        "country": "Iceland",
        "category": "Adventure & Aurora",
        "description": "Gateway to Iceland's natural wonders, geothermal baths, dramatic waterfalls, and Northern Lights.",
        "avg_cost_per_day": 280.0,
        "best_season": "Winter",
    },
]


def seed_firestore():
    print(f"Connecting to Firestore with project ID: {FIRESTORE_PROJECT_ID}...")
    db = firestore.Client(project=FIRESTORE_PROJECT_ID)
    collection_ref = db.collection("destinations")

    for dest in SEED_DESTINATIONS:
        doc_id = dest["id"]
        doc_ref = collection_ref.document(doc_id)
        doc_ref.set(dest)
        print(f"Seeded document '{doc_id}' in 'destinations'")

    print("Firestore seeding complete!")


if __name__ == "__main__":
    seed_firestore()
