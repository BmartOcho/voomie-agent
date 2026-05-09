import pymongo

CONNECTION_STRING = "YOUR_MONGODB_CONNECTION_STRING_HERE"

client = pymongo.MongoClient(CONNECTION_STRING)
collection = client["print_shop"]["active_jobs"]

# 1. Clear out the old test data
collection.delete_many({})

# 2. Define a BAD job (Missing bleed, RGB color)
bad_job = {
    "order_id": "W2P-99042",
    "customer_name": "Acme Corp",
    "status": "new",
    "specs": {"product": "Postcard", "dimensions": "5x7", "stock": "100lb Cover", "bleed_required": True},
    "file_metadata": {"color_space": "RGB", "resolution_dpi": 300, "has_bleed": False},
    "agent_notes": ""
}

# 3. Define a GOOD job (CMYK, has bleed, fits HP Indigo)
good_job = {
    "order_id": "W2P-99043",
    "customer_name": "Tech Corp",
    "status": "new",
    "specs": {"product": "Flyer", "dimensions": "8.5x11", "stock": "100lb Cover", "bleed_required": True},
    "file_metadata": {"color_space": "CMYK", "resolution_dpi": 300, "has_bleed": True},
    "agent_notes": ""
}

# Insert them into MongoDB
collection.insert_many([bad_job, good_job])
print("✅ Database wiped and re-seeded with 1 Good Job and 1 Bad Job!")