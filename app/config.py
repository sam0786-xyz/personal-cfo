"""Central configuration.

Every deployment-specific value is read from the environment so the codebase
carries no personal constants. Defaults are safe for local development.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")

# --- Core services ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GCP_PROJECT = os.environ.get("GCP_PROJECT")  # falls back to ambient credentials

# --- Private access ---
CFO_ACCESS_TOKEN = os.environ.get("CFO_ACCESS_TOKEN")
CFO_AUTH_DISABLED = os.environ.get("CFO_AUTH_DISABLED", "").lower() in {"1", "true", "yes"}

# --- Pub/Sub push verification (see README: Webhook security) ---
# When PUBSUB_AUDIENCE is set, /gmail-webhook requires a valid Google OIDC
# token with this audience. PUBSUB_SERVICE_ACCOUNT additionally pins the
# caller identity to the push subscription's service account.
PUBSUB_AUDIENCE = os.environ.get("PUBSUB_AUDIENCE")
PUBSUB_SERVICE_ACCOUNT = os.environ.get("PUBSUB_SERVICE_ACCOUNT")
PUBSUB_TOPIC = os.environ.get("PUBSUB_TOPIC", "gmail-bank-alerts")

# --- Bank pipeline ---
BANK_ALERT_SENDER = os.environ.get("BANK_ALERT_SENDER", "alerts@axis.bank.in").lower()

# --- Runway model ---
DEFAULT_BALANCE = int(os.environ.get("CFO_DEFAULT_BALANCE", "5000"))
RUNWAY_HORIZON_DAYS = int(os.environ.get("CFO_RUNWAY_HORIZON_DAYS", "30"))
_raw_target = os.environ.get("CFO_TARGET_DATE", "")
TARGET_DATE = datetime.fromisoformat(_raw_target).replace(tzinfo=IST) if _raw_target else None

CFO_PERSONA = os.environ.get("CFO_PERSONA", "a 22-year-old AI engineer")

# --- Asset model seeds (all default to zero; set per deployment) ---
LIQUID_SEED_PRINCIPAL = float(os.environ.get("CFO_LIQUID_SEED_PRINCIPAL", "0"))
LIQUID_APY = float(os.environ.get("CFO_LIQUID_APY", "0.07"))
STAMP_DUTY_RATE = float(os.environ.get("CFO_STAMP_DUTY_RATE", "0.00005"))
SEED_AXIS_EDGE_POINTS = int(os.environ.get("CFO_SEED_AXIS_EDGE_POINTS", "0"))
SEED_GITHUB_CREDITS_USD = int(os.environ.get("CFO_SEED_GITHUB_CREDITS_USD", "0"))
DEFAULT_API_BURN_USD = float(os.environ.get("CFO_DEFAULT_API_BURN_USD", "0"))

# --- Conversion rates (rough heuristics, configurable) ---
AXIS_EDGE_POINT_INR = float(os.environ.get("CFO_AXIS_EDGE_POINT_INR", "0.20"))
USD_INR = float(os.environ.get("CFO_USD_INR", "84"))
FOREX_RATES_INR = {"USD": USD_INR, "AED": 23.0, "EUR": 90.0}
FOREX_MARKUP = 1.02

# --- Storage limits ---
# The wallet document keeps only the most recent transactions; every
# transaction is also written to the append-only `cfo_ledger` collection.
MAX_WALLET_TRANSACTIONS = int(os.environ.get("CFO_MAX_WALLET_TRANSACTIONS", "250"))
MAX_PROCESSED_MESSAGE_IDS = 100

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CFO_ALLOWED_ORIGINS",
        "https://cfo.sam18.xyz,http://localhost:8000",
    ).split(",")
    if origin.strip()
]


def days_until_target() -> int:
    """Days of runway left. Uses CFO_TARGET_DATE when set, otherwise a
    rolling horizon so the daily budget stays meaningful."""
    if TARGET_DATE is None:
        return RUNWAY_HORIZON_DAYS
    today = datetime.now(IST)
    return max((TARGET_DATE.date() - today.date()).days, 1)


def target_date_iso() -> str | None:
    return TARGET_DATE.date().isoformat() if TARGET_DATE else None
