import time

import vertexai
from vertexai.generative_models import (
    FunctionDeclaration,
    GenerativeModel,
    Part,
    Tool,
)
import pymongo
from pymongo.errors import PyMongoError

CONNECTION_STRING = "YOUR_MONGODB_CONNECTION_STRING_HERE"

# 1. Initialize Google Cloud Agent Builder (Vertex AI)
vertexai.init(project="pressflow-hackathon", location="us-central1")

# 2. Define the Prepress Rules (The Brain)
system_instruction = """
You are PressFlow, a senior prepress coordinator.
Your job is to analyze incoming web-to-print job tickets from MongoDB and update their status.

Core Rules:
1. If bleed is required but has_bleed is false, the job CANNOT print.
2. If color_space is RGB, it must be flagged for CMYK conversion.
3. Maximum sheet size for HP Indigo is 13x19.
4. 100lb Cover stock must go to HP Indigo.

Behavior:
Check the active jobs. If a job passes, set status to 'prepress_ready' and assign the machine in notes.
If a job fails, set status to 'Hold - Customer Service' and explain the issue clearly in notes.
"""

# 3. Mirror the MCP Tools for Gemini
get_jobs_func = FunctionDeclaration(
    name="get_new_jobs",
    description="Fetch all web-to-print jobs with status 'new' from MongoDB.",
    parameters={"type": "object", "properties": {}},
)

update_job_func = FunctionDeclaration(
    name="update_job_status",
    description="Update a print job's status and add prepress agent notes.",
    parameters={
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "new_status": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["order_id", "new_status", "notes"],
    },
)

mcp_tool = Tool(function_declarations=[get_jobs_func, update_job_func])

# 4. Initialize the Model
model = GenerativeModel(
    "gemini-2.5-flash",
    system_instruction=[system_instruction],
    tools=[mcp_tool],
)


# ---------------------------------------------------------------------------
# Resilient MongoDB connection helper
# ---------------------------------------------------------------------------
def get_collection(retries: int = 3, delay: float = 2.0):
    """Return the active_jobs collection, retrying on transient failures.

    Returns None if every attempt fails so the caller can degrade gracefully
    instead of crashing the demo.
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            client = pymongo.MongoClient(
                CONNECTION_STRING, serverSelectionTimeoutMS=5000
            )
            # Force a real round-trip so we fail fast if the cluster is down.
            client.admin.command("ping")
            return client["print_shop"]["active_jobs"]
        except PyMongoError as e:
            last_err = e
            print(f"⚠️ MongoDB connect attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(delay)
        except Exception as e:
            last_err = e
            print(f"⚠️ Unexpected MongoDB error attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(delay)
    print(f"❌ MongoDB unreachable after {retries} attempts: {last_err}")
    return None


def safe_send(chat, message):
    """Send a chat message and return (response, error_string_or_None)."""
    try:
        return chat.send_message(message), None
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# 5. Execute the Agent Workflow
# ---------------------------------------------------------------------------
def run_prepress_agent():
    print("🤖 PressFlow AI is waking up...")

    try:
        chat = model.start_chat()
    except Exception as e:
        print(f"❌ Could not start Gemini chat session: {e}")
        return

    response, err = safe_send(
        chat,
        "Check the database for new jobs and process them according to your prepress rules.",
    )
    if err:
        print(f"❌ Initial Gemini call failed: {err}")
        return

    while response and response.candidates and response.candidates[0].function_calls:
        func_call = response.candidates[0].function_calls[0]
        print(f"\n🛠️ Agent is using Tool: {func_call.name}")

        collection = get_collection()
        if collection is None:
            # Database is unreachable — tell Gemini so it can stop cleanly
            # rather than crashing the loop mid-demo.
            response, err = safe_send(
                chat,
                Part.from_function_response(
                    name=func_call.name,
                    response={
                        "error": "MongoDB unavailable. Skipping this tool call."
                    },
                ),
            )
            if err:
                print(f"❌ Could not notify Gemini of DB outage: {err}")
                return
            continue

        # --- TOOL: FETCH JOBS ---
        if func_call.name == "get_new_jobs":
            try:
                jobs = list(collection.find({"status": "new"}, {"_id": 0}))
                print(
                    f"📥 Retrieved {len(jobs)} jobs from MongoDB. "
                    "Handing to AI for prepress analysis..."
                )
                tool_response = {"jobs": jobs}
            except Exception as e:
                print(f"⚠️ get_new_jobs failed: {e}")
                tool_response = {"error": f"Database read failed: {e}"}

            response, err = safe_send(
                chat,
                Part.from_function_response(
                    name="get_new_jobs", response=tool_response
                ),
            )
            if err:
                print(f"❌ Failed to send tool result back to Gemini: {err}")
                return

        # --- TOOL: UPDATE JOB STATUS ---
        elif func_call.name == "update_job_status":
            try:
                args = func_call.args
                order_id = args["order_id"]
                new_status = args["new_status"]
                notes = args["notes"]

                collection.update_one(
                    {"order_id": order_id},
                    {"$set": {"status": new_status, "agent_notes": notes}},
                )
                print(f"✅ Updated MongoDB: Order {order_id} is now '{new_status}'")
                print(f"📝 AI Notes attached: {notes}")
                tool_response = {"status": "success"}
            except KeyError as e:
                print(f"⚠️ update_job_status missing argument: {e}")
                tool_response = {"status": "error", "message": f"Missing arg: {e}"}
            except Exception as e:
                print(f"⚠️ update_job_status failed: {e}")
                tool_response = {"status": "error", "message": str(e)}

            response, err = safe_send(
                chat,
                Part.from_function_response(
                    name="update_job_status", response=tool_response
                ),
            )
            if err:
                print(f"❌ Failed to send tool result back to Gemini: {err}")
                return

        else:
            # Unknown tool — keep the loop alive instead of dying.
            print(f"⚠️ Unknown tool requested: {func_call.name}")
            response, err = safe_send(
                chat,
                Part.from_function_response(
                    name=func_call.name,
                    response={"error": f"Unknown tool: {func_call.name}"},
                ),
            )
            if err:
                print(f"❌ Could not recover from unknown tool: {err}")
                return

    # 6. Final Output
    print("\n🧠 Final Agent Report:")
    try:
        print(response.text if response else "(no response)")
    except Exception as e:
        print(f"⚠️ Could not retrieve final report text: {e}")


if __name__ == "__main__":
    try:
        run_prepress_agent()
    except KeyboardInterrupt:
        print("\n👋 Agent stopped by user.")
    except Exception as e:
        print(f"❌ Fatal error in agent run: {e}")
