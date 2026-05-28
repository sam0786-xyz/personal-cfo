from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types
from dotenv import load_dotenv
from datetime import datetime
import json
import os
from google.cloud import firestore

# Load variables from .env
load_dotenv()

app = FastAPI(title="Action Button CFO Dashboard")

# Enable CORS for easy cross-origin access (e.g. testing from other tools or devices)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the Gemini API client
api_key = os.environ.get("GEMINI_API_KEY")


client = genai.Client(api_key=api_key)

db = firestore.Client()
doc_ref = db.collection("cfo_state").document("wallet")
DEFAULT_BALANCE = 5000

def get_state():
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    return {"current_balance": 5000, "transactions": []} 

def save_state(state):
    doc_ref.set(state)

class ExpenseRequest(BaseModel):
    expense_text: str

@app.get("/cfo-state")
async def cfo_state():
    return get_state()

@app.post("/cfo-reset")
async def cfo_reset():
    state = {"current_balance": DEFAULT_BALANCE, "transactions": []}
    save_state(state)
    return {"status": "success", "message": "Runway reset to ₹5,000.", "state": state}

@app.post("/cfo-check")
async def cfo_check(request: ExpenseRequest):
    state = get_state()
    current_balance = state.get("current_balance", DEFAULT_BALANCE)
    
    # Calculate days until June 22nd 
    target_date = datetime(2026, 6, 22)
    today = datetime.now()
    days_left = max((target_date - today).days, 1)
    
    # THE AGENTIC SYSTEM PROMPT
    system_instruction = (
        f"You are an autonomous financial agent and sarcastic guardian for a 22-year-old AI engineer. "
        f"His current recorded net worth until June 22nd ({days_left} days left) is ₹{current_balance}. "
        f"You must read his input and act as a state-manager by choosing the correct action.\n\n"
        f"YOUR CAPABILITIES (Choose one):\n"
        f"1. ADD_FUNDS: If he receives money (e.g., Eid gifts, stipend), calculate the amount to add. Congratulate him, but warn him not to waste it.\n"
        f"2. RETROACTIVE_DEDUCTION: If he says he ALREADY spent the money (past tense), YOU CANNOT REJECT IT. The money is gone. You MUST deduct it, but you should roast him ruthlessly for his lack of impulse control.\n"
        f"3. REJECT_INTENT: If he is ASKING for permission for a dumb, discretionary spend, harshly reject it and set deduction to 0.\n"
        f"4. APPROVE_INTENT: If he is ASKING for a genuinely necessary, high-ROI spend, approve it.\n\n"
        f"IMPORTANT: Output a valid JSON with these EXACT keys:\n"
        f"'message' (your vocal response),\n"
        f"'funds_added' (integer, 0 if no money received),\n"
        f"'expense_deducted' (integer, 0 if rejected or no expense),\n"
        f"'action_taken' (string: strictly 'ADD_FUNDS', 'RETROACTIVE_DEDUCTION', 'REJECT_INTENT', or 'APPROVE_INTENT')."
    )
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=request.expense_text,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                safety_settings=[
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH, 
                    ),
                ],
            ),
        )
        
        decision = json.loads(response.text.strip())
        
        # Extract agent's autonomous actions
        funds_added = int(decision.get("funds_added", 0))
        expense_deducted = int(decision.get("expense_deducted", 0))
        action_taken = decision.get("action_taken", "UNKNOWN")
        
        # Security Safeguards
        if funds_added < 0: funds_added = 0
        if expense_deducted < 0: expense_deducted = 0
        
        # Enforce rejection logic
        if action_taken == "REJECT_INTENT":
            expense_deducted = 0
            
        # Check for overdrafts (after adding any new funds)
        temp_balance = current_balance + funds_added
        if expense_deducted > temp_balance:
            attempted_spend = expense_deducted
            expense_deducted = 0
            decision["action_taken"] = "REJECT_INTENT"
            decision["message"] = f"REJECTED: You tried to spend ₹{attempted_spend} but you only have ₹{temp_balance} left. Bankrupt behavior. Denied."
            
        # Execute the math
        new_balance = temp_balance - expense_deducted
        state["current_balance"] = new_balance
            
        # Log the complex transaction
        transaction = {
            "timestamp": datetime.now().isoformat(),
            "expense_text": request.expense_text,
            "action_taken": decision.get("action_taken"),
            "funds_added": funds_added,
            "approved_amount": expense_deducted,
            "message": decision.get("message", ""),
            "remaining_balance": new_balance
        }
        state["transactions"].insert(0, transaction) 
        
        save_state(state)
        
        # Pass the final numbers back to the client
        decision["current_balance"] = new_balance
        return decision

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent Error: {str(e)}")

# Mount static files directory at the root AFTER defining specific API routes
os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
