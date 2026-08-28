from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx

from app.config import Settings


@dataclass(slots=True)
class Attachment:
    filename: str
    content_type: str
    content: bytes


@dataclass(slots=True)
class MailMessage:
    message_id: str
    subject: str
    sender: str
    attachments: list[Attachment]


class GraphMailboxAdapter:
    """Application-permission Microsoft Graph adapter for a shared AP mailbox."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str, mailbox: str, folder: str = "Inbox") -> None:
        self.tenant_id, self.client_id, self.client_secret = tenant_id, client_id, client_secret
        self.mailbox, self.folder = mailbox, folder

    @classmethod
    def from_settings(cls, settings: Settings) -> "GraphMailboxAdapter":
        required = [settings.graph_tenant_id, settings.graph_client_id, settings.graph_client_secret, settings.graph_mailbox]
        if not all(required):
            raise RuntimeError("Microsoft Graph mailbox credentials are not configured")
        return cls(*required, settings.graph_folder)  # type: ignore[arg-type]

    def _token(self) -> str:
        response = httpx.post(
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
            data={"client_id": self.client_id, "client_secret": self.client_secret, "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def fetch_unread(self, limit: int = 10) -> list[MailMessage]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        base = f"https://graph.microsoft.com/v1.0/users/{self.mailbox}/mailFolders/{self.folder}/messages"
        response = httpx.get(base, headers=headers, params={"$filter": "isRead eq false and hasAttachments eq true", "$top": limit, "$select": "id,subject,from"}, timeout=30)
        response.raise_for_status()
        messages = []
        for item in response.json().get("value", []):
            attachments_response = httpx.get(f"{base}/{item['id']}/attachments", headers=headers, timeout=30)
            attachments_response.raise_for_status()
            attachments = []
            for attachment in attachments_response.json().get("value", []):
                encoded = attachment.get("contentBytes")
                filename = attachment.get("name", "")
                if encoded and filename.lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".html", ".htm", ".json", ".txt")):
                    attachments.append(Attachment(filename, attachment.get("contentType", "application/octet-stream"), base64.b64decode(encoded)))
            messages.append(MailMessage(item["id"], item.get("subject", ""), item.get("from", {}).get("emailAddress", {}).get("address", ""), attachments))
        return messages

