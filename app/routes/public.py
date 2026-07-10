"""Public endpoints: health, project metadata, and session unlock."""

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app import config, security

router = APIRouter()


class AuthSessionRequest(BaseModel):
    token: str


@router.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "personal-cfo"}


@router.get("/public-profile")
async def public_profile():
    return {
        "title": "AgentCFO",
        "tagline": "A private AI finance cockpit that turns messy bank alerts into decisions, runway, and action.",
        "privacy_note": "Live balances, transactions, debts, bank sync status, and personal audit logs are private.",
        "public_activity": [
            {
                "label": "Ground-truth ingestion",
                "detail": "Built a Gmail-to-Firestore pipeline for bank transaction capture with OTP redaction before model parsing.",
            },
            {
                "label": "Decision engine",
                "detail": "Connected Gemini to classify money intents, lending, collections, and spend approvals from natural language.",
            },
            {
                "label": "Runway dashboard",
                "detail": "Built a FastAPI and vanilla JS cockpit for cash runway, debts, liquid reserves, and bank-sync telemetry.",
            },
            {
                "label": "Next build",
                "detail": "Moving from raw audit feed to categorized spending, rewards inventory, obligations, and proactive CFO recommendations.",
            },
        ],
        "stack": ["FastAPI", "Firestore", "Gmail API", "Pub/Sub", "Gemini", "Cloud Run", "Vanilla JS"],
    }


@router.post("/auth/session")
async def auth_session(auth_request: AuthSessionRequest, response: Response):
    if config.CFO_AUTH_DISABLED:
        response.set_cookie(
            "cfo_access_token",
            auth_request.token,
            httponly=True,
            secure=False,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )
        return {"status": "success"}
    if not config.CFO_ACCESS_TOKEN:
        raise HTTPException(status_code=503, detail="Private CFO access is not configured.")
    supplied_token = (auth_request.token or "").strip()
    if not security.verify_session_token(supplied_token):
        raise HTTPException(status_code=401, detail="Invalid private access token.")
    response.set_cookie(
        "cfo_access_token",
        supplied_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return {"status": "success"}
