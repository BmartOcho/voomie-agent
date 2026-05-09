from mcp.server.fastmcp import FastMCP
import pymongo

# 1. Initialize MCP Server
mcp = FastMCP("PressFlow_MongoDB")

# 2. Database Connection
CONNECTION_STRING = "YOUR_MONGODB_CONNECTION_STRING_HERE"
client = pymongo.MongoClient(CONNECTION_STRING)
db = client["print_shop"]
collection = db["active_jobs"]

# 3. Define Tools for the Agent
@mcp.tool()
def get_new_jobs() -> str:
    """Fetch all web-to-print jobs with status 'new'."""
    jobs = list(collection.find({"status": "new"}, {"_id": 0})) # _id excluded for clean JSON
    if not jobs:
        return "No new jobs found."
    return str(jobs)

@mcp.tool()
def update_job_status(order_id: str, new_status: str, notes: str) -> str:
    """Update a print job's status and add prepress agent notes."""
    result = collection.update_one(
        {"order_id": order_id},
        {"$set": {"status": new_status, "agent_notes": notes}}
    )
    if result.modified_count > 0:
        return f"Success: Order {order_id} updated to '{new_status}'."
    return f"Error: Order {order_id} not found."

if __name__ == "__main__":
    # Run the server via stdio (standard for MCP integration)
    mcp.run()