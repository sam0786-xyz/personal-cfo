"""Access control and the deterministic OTP firewall.

Layer 1 lives in Gmail (label filter). Layers 2 and 3 live here so the
pipeline never depends on Gmail filters behaving: the subject gate rejects
anything that is not clearly a transaction alert, and the body sanitizer
redacts OTP-shaped content before any text reaches the LLM.
"""

import hmac
import logging
import re

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app import config

logger = logging.getLogger("personal-cfo")

# --- Private dashboard access ---


def _extract_access_token(request: Request) -> str:
    """Read the private dashboard token from the Authorization header,
    X-CFO-Token header, or the session cookie. Query-string tokens are
    deliberately not accepted server-side: they end up in access logs."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return (
        request.headers.get("x-cfo-token")
        or request.cookies.get("cfo_access_token")
        or ""
    ).strip()


def require_private_access(request: Request):
    """Protect personal finance data from public visitors."""
    if config.CFO_AUTH_DISABLED:
        return
    if not config.CFO_ACCESS_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Private CFO access is not configured. Set CFO_ACCESS_TOKEN or CFO_AUTH_DISABLED=true for local development.",
        )
    supplied_token = _extract_access_token(request)
    if not supplied_token or not hmac.compare_digest(supplied_token, config.CFO_ACCESS_TOKEN):
        raise HTTPException(status_code=401, detail="Private CFO access required.")


def verify_session_token(supplied_token: str) -> bool:
    if not config.CFO_ACCESS_TOKEN or not supplied_token:
        return False
    return hmac.compare_digest(supplied_token, config.CFO_ACCESS_TOKEN)


# --- Pub/Sub push verification ---

_warned_unverified = False


def verify_pubsub_push(request: Request):
    """Verify the Google-signed OIDC token on a Pub/Sub push request.

    Enforced when PUBSUB_AUDIENCE is configured; otherwise logs a warning so
    an unverified deployment is visible in Cloud Run logs.
    """
    global _warned_unverified
    if not config.PUBSUB_AUDIENCE:
        if not _warned_unverified:
            logger.warning(
                "PUBSUB_AUDIENCE is not set — /gmail-webhook is accepting unverified pushes. "
                "See README 'Webhook security' to enable OIDC verification."
            )
            _warned_unverified = True
        return

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Pub/Sub OIDC token.")
    token = auth_header[7:].strip()
    try:
        claims = id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=config.PUBSUB_AUDIENCE
        )
    except Exception as exc:
        logger.warning(f"Rejected Pub/Sub push with invalid OIDC token: {exc}")
        raise HTTPException(status_code=401, detail="Invalid Pub/Sub OIDC token.") from exc

    if config.PUBSUB_SERVICE_ACCOUNT:
        email = str(claims.get("email", "")).lower()
        if not claims.get("email_verified") or email != config.PUBSUB_SERVICE_ACCOUNT.lower():
            logger.warning(f"Rejected Pub/Sub push from unexpected identity: {email or 'unknown'}")
            raise HTTPException(status_code=403, detail="Unexpected Pub/Sub caller identity.")


# --- Layer 2: subject-line gate (deterministic) ---

TRANSACTION_KEYWORDS = [
    "debited", "credited", "transaction alert", "a/c no",
    "withdrawn", "received", "transfer", "payment",
    "purchase", "refund", "reversed", "emi", "auto-debit",
    "neft", "imps", "upi", "standing instruction",
    "amount", "inr",
]

BLOCK_KEYWORDS = [
    "otp", "one time password", "password", "pin",
    "verification", "verify", "login", "authenticate",
    "reset", "security code", "2fa", "do not share",
    "temporary password",
]


def is_safe_transaction_email(subject: str) -> bool:
    """Layer 2: allow a subject only when it looks like a transaction alert
    and contains no credential-related keyword. Unknown subjects are denied."""
    subject_lower = (subject or "").lower()
    if any(kw in subject_lower for kw in BLOCK_KEYWORDS):
        return False
    if any(kw in subject_lower for kw in TRANSACTION_KEYWORDS):
        return True
    return False


# --- Layer 3: body sanitizer (deterministic) ---


def sanitize_body(raw_body: str) -> str:
    """Layer 3: redact OTP-shaped content before the body reaches the LLM.
    Redacting (instead of dropping the email) lets transaction alerts with
    standard 'never share your OTP' footers still be processed."""
    redacted = re.sub(r"(?i)(otp|pin|password|code)[\s\-:]*(\d{4,8})", r"\1 [BLOCKED]", raw_body or "")
    # Standalone 6-digit numbers are overwhelmingly OTPs in bank mail;
    # rupee amounts that large carry comma separators and survive this.
    redacted = re.sub(r"\b\d{6}\b", "[BLOCKED]", redacted)
    return redacted
