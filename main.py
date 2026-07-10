"""AgentCFO — FastAPI application entrypoint.

All logic lives in the ``app`` package:
- app.config          environment-driven settings
- app.security        private access, OTP firewall, Pub/Sub OIDC
- app.state           transactional Firestore state + append-only ledger
- app.classification  rule cascade + human-in-the-loop learning
- app.agent           CFO decision math (pure, unit-tested)
- app.llm             Gemini structured-output calls
- app.gmail_pipeline  Gmail history processing
"""

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import config
from app.routes import cfo, gmail, public

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AgentCFO")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Force browsers to revalidate HTML on every load (prevents stale cache in Safari)
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.include_router(public.router)
app.include_router(cfo.router)
app.include_router(gmail.router)

os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
