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
    
    # Calculate days until June 22nd (for dynamic roasting context)
    target_date = datetime(2026, 6, 22)
    today = datetime.now()
    days_left = max((target_date - today).days, 1)
    
    system_instruction = (
        f"You are an aggressive, highly analytical, and extremely sarcastic financial guardian for a 22-year-old AI engineer. "
        f"His remaining total net worth until June 22nd (exactly {days_left} days left) is exactly ₹{current_balance}. "
        f"His food and shelter are covered at home, meaning ANY expense is purely discretionary or professional. "
        f"When he inputs an intended purchase, instantly calculate the percentage of his remaining net worth it consumes. "
        f"Analyze the ROI. If it is a bad, non-essential spend, harshly reject it and insult his poor choices in 2-3 sentences. "
        f"If it is a genuinely necessary, high-ROI spend (e.g. key server costs, vital AI learning resources, crucial network infrastructure), approve it. "
        f"IMPORTANT: You must output a valid JSON response with two keys: "
        f"'message' (your aggressive vocal response to him) and 'approved_amount' (the integer amount to deduct, which MUST be 0 if rejected or if he cannot afford it)."
    )
    
    try:
        # Using the official new Google GenAI SDK (client.models.generate_content)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=request.expense_text,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                safety_settings=[
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH, # or BLOCK_NONE
                    ),
                ],
            ),
        )
        
        # Parse Gemini's JSON output
        decision = json.loads(response.text.strip())
        
        # Extract approved amount
        deduction = int(decision.get("approved_amount", 0))
        
        # Safeguards to prevent overdrafts or negative approvals
        if deduction < 0:
            deduction = 0
            decision["approved_amount"] = 0
        if deduction > current_balance: 
            attempted_spend = deduction
            deduction = 0
            decision["approved_amount"] = 0
            decision["message"] = f"REJECTED: You wanted to spend ₹{attempted_spend} but you only have ₹{current_balance} left. You are literally bankrupt. Rejecting."
        # Deduct the money if approved
        new_balance = current_balance
        if deduction > 0:
            new_balance = current_balance - deduction
            state["current_balance"] = new_balance
            
        # Log the transaction
        transaction = {
            "timestamp": datetime.now().isoformat(),
            "expense_text": request.expense_text,
            "approved_amount": deduction,
            "message": decision.get("message", ""),
            "remaining_balance": new_balance
        }
        state["transactions"].insert(0, transaction) # Add to the top of the feed
        
        save_state(state)
        
        # Return final decision to the client
        return decision

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini API or Parse Error: {str(e)}")

# Mount static files directory at the root AFTER defining specific API routes
os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
