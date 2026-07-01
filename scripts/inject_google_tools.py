#!/usr/bin/env python3
"""Inject Google Calendar + Gmail tools into Open WebUI's function table.

Uses the google-api-python-client + google-auth already present in Open WebUI.
Idempotent — re-run to update.
"""

import argparse
import json
import os
import sqlite3
import time
import uuid

FUNCTION_ID = "a1b2c3d4-google-calendar-gmail"

DEFAULT_DB = os.path.expanduser("~/.model-router/runtime/open-webui/webui.db")

FUNCTION_CONTENT = r'''
"""
title: Google Calendar & Gmail
author: model-router
description: List/create Google Calendar events and send Gmail messages.
version: 2.0
requires: google-api-python-client, google-auth
"""

import json
import base64
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from pydantic import BaseModel, Field


_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
]


def _credentials():
    raw = getattr(Tools.valves, "GOOGLE_CREDENTIALS_JSON", "") or ""
    return service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=_SCOPES
    )


class Tools:
    class Valves(BaseModel):
        GOOGLE_CREDENTIALS_JSON: str = Field(
            default="",
            description="Paste the full service-account JSON key here",
        )

    class UserValves(BaseModel):
        DELEGATE_EMAIL: str = Field(
            default="",
            description="Optional: email to impersonate via domain-wide delegation",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    async def list_calendar_events(
        self, __event_emitter__, max_results: int = 10, time_min: Optional[str] = None
    ) -> str:
        """List upcoming Google Calendar events.

        :param max_results: Max events (1-250).
        :param time_min: RFC3339 lower bound, e.g. 2026-07-01T00:00:00Z. Defaults to now.
        """
        if not self.valves.GOOGLE_CREDENTIALS_JSON:
            return "Error: GOOGLE_CREDENTIALS_JSON valve is empty."

        if time_min is None:
            time_min = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        service = build("calendar", "v3", credentials=_credentials())
        events = (
            service.events()
            .list(
                calendarId="primary",
                maxResults=min(max(max_results, 1), 250),
                timeMin=time_min,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        items = events.get("items", [])
        if not items:
            return "No upcoming events found."

        lines = []
        for ev in items:
            start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "?"))
            lines.append(f"- {start}: {ev.get('summary', '(no title)')}")
        return "\n".join(lines)

    async def create_calendar_event(
        self,
        __event_emitter__,
        summary: str,
        start_time: str,
        end_time: str,
        description: Optional[str] = None,
    ) -> str:
        """Create a Google Calendar event.

        :param summary: Event title.
        :param start_time: RFC3339 start, e.g. 2026-07-01T14:00:00.
        :param end_time: RFC3339 end.
        :param description: Optional description.
        """
        if not self.valves.GOOGLE_CREDENTIALS_JSON:
            return "Error: GOOGLE_CREDENTIALS_JSON valve is empty."

        body = {
            "summary": summary,
            "start": {"dateTime": start_time, "timeZone": "UTC"},
            "end": {"dateTime": end_time, "timeZone": "UTC"},
        }
        if description:
            body["description"] = description

        service = build("calendar", "v3", credentials=_credentials())
        result = service.events().insert(calendarId="primary", body=body).execute()
        return f"Event created: {result.get('htmlLink', result.get('id', '?'))}"

    async def send_gmail(
        self, __event_emitter__, to: str, subject: str, body: str
    ) -> str:
        """Send an email via Gmail.

        :param to: Recipient address.
        :param subject: Email subject.
        :param body: Plain-text body.
        """
        if not self.valves.GOOGLE_CREDENTIALS_JSON:
            return "Error: GOOGLE_CREDENTIALS_JSON valve is empty."

        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        service = build("gmail", "v1", credentials=_credentials())
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return f"Email sent to {to}"
'''


def inject(db_path: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        now = int(time.time())
        admin = conn.execute("SELECT id FROM user LIMIT 1").fetchone()
        if not admin:
            print("No users in webui.db — sign up first.")
            return False
        user_id = admin[0]

        meta = json.dumps({
            "description": "Google Calendar + Gmail via service account",
            "manifest": {"title": "Google Calendar & Gmail", "author": "model-router", "version": "2.0"},
        })
        valves = json.dumps({"GOOGLE_CREDENTIALS_JSON": ""})

        existing = conn.execute("SELECT id FROM function WHERE id = ?", (FUNCTION_ID,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE function SET content=?, meta=?, valves=?, updated_at=? WHERE id=?",
                (FUNCTION_CONTENT, meta, valves, now, FUNCTION_ID),
            )
            print("Updated existing Google Calendar & Gmail function.")
        else:
            conn.execute(
                "INSERT INTO function (id,user_id,name,type,content,meta,valves,is_active,is_global,updated_at,created_at) "
                "VALUES (?,?,?,?,?,?,?,1,1,?,?)",
                (FUNCTION_ID, user_id, "Google Calendar & Gmail", "tool",
                 FUNCTION_CONTENT, meta, valves, now, now),
            )
            print("Injected Google Calendar & Gmail function.")
        conn.commit()
        return True
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Inject Google tools into Open WebUI")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to webui.db")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"Database not found: {args.db}")
        return

    inject(args.db)
    print("\nNext: Admin > Functions > Google Calendar & Gmail > Edit Valve")
    print("  GOOGLE_CREDENTIALS_JSON → paste service-account JSON key")


if __name__ == "__main__":
    main()
