import pymongo

# 1. Paste your "Golden Ticket" connection string right here inside the quotes
CONNECTION_STRING = "YOUR_MONGODB_CONNECTION_STRING_HERE"

def test_connection():
    try:
        # Connect to the MongoDB cluster
        client = pymongo.MongoClient(CONNECTION_STRING)
        
        # Create a database called "print_shop" and a collection (folder) called "active_jobs"
        db = client["print_shop"]
        collection = db["active_jobs"]
        
        # Create a dummy web-to-print order
        dummy_job = {
            "order_id": "W2P-001",
            "customer_name": "Hackathon Judges",
            "status": "new",
            "specs": {"product": "Business Cards", "dimensions": "3.5x2"}
        }
        
        # Insert the job into the database
        insert_result = collection.insert_one(dummy_job)
        print(f"✅ Success! Connected to MongoDB and inserted job ID: {insert_result.inserted_id}")
        
        # Read it back to prove it worked
        retrieved_job = collection.find_one({"order_id": "W2P-001"})
        print(f"📂 Retrieved from database: {retrieved_job['customer_name']} - {retrieved_job['specs']['product']}")
        
    except Exception as e:
        print(f"❌ Connection failed. Error: {e}")

if __name__ == "__main__":
    test_connection()