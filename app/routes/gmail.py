"""Gmail pipeline endpoints: Pub/Sub webhook, watch renewal, sync status."""

import base64
import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app import config, gmail_pipeline, security, state
from app.config import IST

logger = logging.getLogger("personal-cfo")
router = APIRouter()


@router.post("/gmail-webhook")
async def gmail_webhook(request: Request):
    """Receives Pub/Sub push notifications for new Gmail messages and runs
    them through the 3-layer security filter before reconciliation."""
    # Auth failures return 4xx so a misconfigured or malicious caller is
    # visible; processing errors return 200 to stop Pub/Sub retry storms.
    security.verify_pubsub_push(request)
    try:
        envelope = await request.json()
        message = envelope.get("message", {})

        if not message.get("data"):
            return {"status": "ignored", "reason": "no data in message"}

        decoded = base64.b64decode(message["data"]).decode("utf-8")
        payload = json.loads(decoded)
        history_id = payload.get("historyId")

        if not history_id:
            return {"status": "ignored", "reason": "no historyId"}

        return await run_in_threadpool(gmail_pipeline.process_history_update, history_id)

    except Exception as e:
        logger.error(f"Gmail webhook error: {e}")
        return {"status": "error", "detail": str(e)}


@router.post("/gmail-watch")
async def gmail_watch(request: Request):
    """Register or renew Gmail push notifications. Called by Cloud Scheduler
    every 3 days to keep the watch alive."""
    security.require_private_access(request)
    try:
        service = gmail_pipeline.get_gmail_service()
        sync_state = state.get_gmail_sync_state()

        request_body = {
            "topicName": f"projects/{config.GCP_PROJECT}/topics/{config.PUBSUB_TOPIC}"
        }
        response = service.users().watch(userId="me", body=request_body).execute()

        sync_state["watch_expiration"] = response.get("expiration")
        if response.get("historyId"):
            sync_state["last_history_id"] = response["historyId"]
        sync_state["last_sync_at"] = datetime.now(IST).isoformat()
        state.save_gmail_sync_state(sync_state)

        return {
            "status": "success",
            "expiration": response.get("expiration"),
            "historyId": response.get("historyId"),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gmail watch error: {str(e)}")


@router.get("/gmail-status")
async def gmail_status(request: Request):
    security.require_private_access(request)
    sync_state = state.get_gmail_sync_state()
    return {
        "watch_active": sync_state.get("watch_expiration") is not None,
        "watch_expiration": sync_state.get("watch_expiration"),
        "last_sync_at": sync_state.get("last_sync_at"),
        "total_synced": sync_state.get("total_synced", 0),
        "label_id": sync_state.get("label_id"),
        "last_history_id": sync_state.get("last_history_id"),
    }


@router.post("/gmail-set-label")
async def gmail_set_label(label_id: str, request: Request):
    """Set the Gmail label ID used for filtering. Call once after creating the label."""
    security.require_private_access(request)
    sync_state = state.get_gmail_sync_state()
    sync_state["label_id"] = label_id
    state.save_gmail_sync_state(sync_state)
    return {"status": "success", "label_id": label_id}
