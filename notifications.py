"""Optional Teams and Outlook notification adapters.

All network delivery is opt-in. Secrets are read from environment variables and
are never written to localStorage, screenshots, or the repository.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.request import Request, urlopen


def _secret(name: str) -> str | None:
    """Read environment secrets first, then the ignored local secrets file."""
    value = os.environ.get(name)
    if value:
        return value
    path = Path(__file__).with_name("secrets.local.json")
    if not path.exists():
        return None
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        return values.get(name)
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class NotificationEvent:
    event: str
    queue_id: str
    queue: str
    queue_type: str
    wait_text: str
    wait_seconds: int
    threshold_seconds: int


class Notifier:
    def __init__(self, mode: str = "disabled") -> None:
        if mode not in {"disabled", "teams", "outlook", "both"}:
            raise ValueError("Notification mode must be disabled, teams, outlook, or both.")
        self.mode = mode

    def send(self, event: NotificationEvent) -> None:
        if self.mode == "disabled":
            return
        errors = []
        if self.mode in {"teams", "both"}:
            try:
                self._send_teams(event)
            except Exception as error:
                errors.append(f"Teams: {error}")
        if self.mode in {"outlook", "both"}:
            try:
                self._send_outlook(event)
            except Exception as error:
                errors.append(f"Outlook: {error}")
        if errors:
            raise RuntimeError("; ".join(errors))
        print(f"Notification sent: {event.event} for {event.queue}")

    def _send_teams(self, event: NotificationEvent) -> None:
        managed_queue_ids = {"eec-calls", "eec-chats"}
        channel = "managed-services" if event.queue_id in managed_queue_ids else "support-services"
        webhook = _secret(f"NICE_TEAMS_{channel.upper().replace('-', '_')}_WEBHOOK_URL")
        if not webhook:
            raise RuntimeError(f"NICE_TEAMS_{channel.upper().replace('-', '_')}_WEBHOOK_URL is not configured")
        mention_id = _secret("NICE_SERVICE_COORDINATOR_MENTION_ID") if event.queue_id == "service-coordinators-calls" else None
        mention_name = _secret("NICE_SERVICE_COORDINATOR_MENTION_NAME") or "Service Coordinator"
        mention_text = f"<at>{mention_name}</at> " if mention_id else ("@Service Coordinator " if event.queue_id == "service-coordinators-calls" else "")
        message = f"{mention_text}NICE Queue {event.event}\n{event.queue}\nLongest wait: {event.wait_text}\nThreshold: {event.threshold_seconds // 60:02d}:00"
        if mention_id:
            payload = {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.2", "body": [{"type": "TextBlock", "text": message, "wrap": True}], "msteams": {"entities": [{"type": "mention", "text": f"<at>{mention_name}</at>", "mentioned": {"id": mention_id, "name": mention_name}}]}}}]}
        else:
            payload = {"text": message}
        request = Request(webhook, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=15) as response:
            if response.status >= 300:
                raise RuntimeError(f"HTTP {response.status}")

    def _send_outlook(self, event: NotificationEvent) -> None:
        host = _secret("NICE_OUTLOOK_SMTP_HOST")
        port = int(_secret("NICE_OUTLOOK_SMTP_PORT") or "587")
        username = _secret("NICE_OUTLOOK_SMTP_USERNAME")
        password = _secret("NICE_OUTLOOK_SMTP_PASSWORD")
        sender = _secret("NICE_OUTLOOK_FROM") or username or ""
        recipient = _secret("NICE_OUTLOOK_TO")
        if not all((host, username, password, sender, recipient)):
            raise RuntimeError("Outlook SMTP variables are incomplete")
        message = EmailMessage()
        message["Subject"] = f"NICE Queue {event.event}: {event.queue}"
        message["From"], message["To"] = sender, recipient
        message.set_content(f"Queue: {event.queue}\nType: {event.queue_type}\nLongest wait: {event.wait_text}\nSeconds: {event.wait_seconds}\nThreshold: {event.threshold_seconds}\nEvent: {event.event}")
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls(context=context)
            smtp.login(username, password)
            smtp.send_message(message)
