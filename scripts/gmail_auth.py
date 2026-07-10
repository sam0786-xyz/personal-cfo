#!/usr/bin/env python3
"""
One-time Gmail OAuth2 consent script.
Run this ONCE locally to authenticate and obtain a refresh token.

Usage:
    1. Download your OAuth2 client_secret.json from Google Cloud Console
    2. Place it in this directory (scripts/)
    3. Run: python scripts/gmail_auth.py
    4. A browser will open — log in with the Gmail account that receives bank alerts
    5. Copy the printed refresh token into Secret Manager
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main():
    # Look for client_secret.json in the scripts/ directory or project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)

    secret_path = None
    for search_dir in [script_dir, project_dir]:
        candidate = os.path.join(search_dir, "client_secret.json")
        if os.path.exists(candidate):
            secret_path = candidate
            break

    if not secret_path:
        # Check for any file matching client_secret*.json
        for search_dir in [script_dir, project_dir]:
            for f in os.listdir(search_dir):
                if f.startswith("client_secret") and f.endswith(".json"):
                    secret_path = os.path.join(search_dir, f)
                    break

    if not secret_path:
        print("\nERROR: client_secret.json not found!")
        print("   Download it from: Console → APIs & Services → Credentials → OAuth 2.0 Client IDs")
        print(f"   Place it in: {script_dir}/client_secret.json")
        sys.exit(1)

    gcp_project = os.environ.get("GCP_PROJECT", "YOUR_PROJECT_ID")

    print(f"Found credentials: {secret_path}")
    print(f"Scope: {SCOPES[0]} (read-only)")
    print("\nOpening browser for authentication...")
    print("   → Log in with the Gmail account that receives your bank alerts\n")

    flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPES)
    creds = flow.run_local_server(port=8080, prompt="consent")

    print("\n" + "=" * 60)
    print("AUTHENTICATION SUCCESSFUL")
    print("=" * 60)
    print(f"\nRefresh Token:\n{creds.refresh_token}")
    print(f"\nClient ID:\n{creds.client_id}")
    print(f"\nClient Secret:\n{creds.client_secret}")
    print("\n" + "=" * 60)
    print("\nNow store these in Secret Manager:")
    for secret_id, value in [
        ("gmail-refresh-token", creds.refresh_token),
        ("gmail-client-id", creds.client_id),
        ("gmail-client-secret", creds.client_secret),
    ]:
        print(f'  echo -n "{value}" | gcloud secrets create {secret_id} --data-file=- --project={gcp_project}')
    print("=" * 60)


if __name__ == "__main__":
    main()
