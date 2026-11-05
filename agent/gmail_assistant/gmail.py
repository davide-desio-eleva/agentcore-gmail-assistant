"""Gmail API wrapper. OAuth credentials are read from SSM Parameter Store, so nothing sensitive is baked into the image."""

import base64
import html
import json
import re
from typing import Any

import boto3
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# gmail.modify allows reading and changing labels; it cannot permanently delete mail.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
SUMMARY_HEADERS = ["From", "To", "Subject", "Date", "List-Unsubscribe"]
MAX_BODY_CHARS = 6000

# Gmail system labels that must not be used as folders by this assistant.
RESERVED_LABELS = {"INBOX", "UNREAD", "STARRED", "IMPORTANT", "SENT", "DRAFT", "SPAM", "TRASH", "CHAT"}


class GmailClient:
    def __init__(self, service: Any):
        self._service = service

    @classmethod
    def from_parameter(cls, name: str) -> "GmailClient":
        raw = boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
        secret = json.loads(raw)
        credentials = Credentials(
            token=None,
            refresh_token=secret["refresh_token"],
            token_uri=TOKEN_URI,
            client_id=secret["client_id"],
            client_secret=secret["client_secret"],
            scopes=GMAIL_SCOPES,
        )
        return cls(build("gmail", "v1", credentials=credentials, cache_discovery=False))

    def list_messages(self, query: str, max_results: int) -> list[dict]:
        response = self._service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        return [self._summary(message["id"]) for message in response.get("messages", [])]

    def read_message(self, message_id: str) -> dict:
        message = self._service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = _headers(message)
        body = extract_text(message.get("payload", {}))
        return {
            "id": message["id"],
            "from": headers.get("from", ""),
            "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""),
            "body": body[:MAX_BODY_CHARS],
            "truncated": len(body) > MAX_BODY_CHARS,
            "unread": "UNREAD" in message.get("labelIds", []),
            "attachments": _attachment_parts(message.get("payload", {})),
        }

    def list_attachments(self, message_id: str) -> list[dict]:
        message = self._service.users().messages().get(userId="me", id=message_id, format="full").execute()
        return _attachment_parts(message.get("payload", {}))

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        data = (
            self._service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()["data"]
        )
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

    def list_folders(self) -> list[str]:
        return sorted(label["name"] for label in self._labels() if label["type"] == "user")

    def mark_as_read(self, message_ids: list[str]) -> None:
        self._batch_modify(message_ids, remove=["UNREAD"])

    def move_to_folder(self, message_ids: list[str], folder: str) -> str:
        """Move emails out of the inbox into a folder. Returns the label id of the folder."""
        if folder.upper() in RESERVED_LABELS:
            raise ValueError(f"'{folder}' is a Gmail system label and cannot be used as a folder.")
        label_id = self._get_or_create_label(folder)
        self._batch_modify(message_ids, add=[label_id], remove=["INBOX"])
        return label_id

    def set_starred(self, message_ids: list[str], starred: bool) -> None:
        """Star or unstar emails. The star is Gmail's STARRED label, which the user sees as important."""
        if starred:
            self._batch_modify(message_ids, add=["STARRED"])
        else:
            self._batch_modify(message_ids, remove=["STARRED"])

    def trash(self, message_ids: list[str]) -> None:
        """Move emails to the Trash. Gmail keeps them there for 30 days."""
        for message_id in message_ids:
            self._service.users().messages().trash(userId="me", id=message_id).execute()

    def untrash(self, message_ids: list[str]) -> None:
        for message_id in message_ids:
            self._service.users().messages().untrash(userId="me", id=message_id).execute()

    def label_ids(self, message_ids: list[str]) -> dict[str, set[str]]:
        """The labels of each email now, so that an undo puts back only what an action changed."""
        return {
            message_id: set(self._service.users().messages().get(userId="me", id=message_id, format="minimal").execute().get("labelIds", []))
            for message_id in message_ids
        }

    def restore(self, message_ids: list[str], add: list[str], remove: list[str]) -> None:
        """Reverse an earlier action by adding and removing labels."""
        self._batch_modify(message_ids, add=add or None, remove=remove or None)

    def unsubscribe_header(self, message_id: str) -> str:
        message = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata", metadataHeaders=["From", "List-Unsubscribe"])
            .execute()
        )
        headers = _headers(message)
        return headers.get("list-unsubscribe", "")

    def _summary(self, message_id: str) -> dict:
        message = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata", metadataHeaders=SUMMARY_HEADERS)
            .execute()
        )
        headers = _headers(message)
        return {
            "id": message["id"],
            "from": headers.get("from", ""),
            "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""),
            "snippet": html.unescape(message.get("snippet", "")),
            "unread": "UNREAD" in message.get("labelIds", []),
            "can_unsubscribe": bool(headers.get("list-unsubscribe")),
        }

    def _labels(self) -> list[dict]:
        return self._service.users().labels().list(userId="me").execute().get("labels", [])

    def _get_or_create_label(self, name: str) -> str:
        for label in self._labels():
            if label["name"].lower() == name.lower():
                return label["id"]
        body = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        return self._service.users().labels().create(userId="me", body=body).execute()["id"]

    def _batch_modify(self, message_ids: list[str], add: list[str] | None = None, remove: list[str] | None = None) -> None:
        if not message_ids:
            return
        body: dict[str, Any] = {"ids": message_ids}
        if add:
            body["addLabelIds"] = add
        if remove:
            body["removeLabelIds"] = remove
        self._service.users().messages().batchModify(userId="me", body=body).execute()


def extract_text(payload: dict) -> str:
    """Return the plain-text body of a message, falling back to its HTML part."""
    plain = _collect_part(payload, "text/plain")
    if plain.strip():
        return plain.strip()
    return _strip_html(_collect_part(payload, "text/html"))


def _collect_part(part: dict, mime_type: str) -> str:
    if part.get("mimeType") == mime_type and part.get("body", {}).get("data"):
        return _decode(part["body"]["data"])
    return "".join(_collect_part(child, mime_type) for child in part.get("parts", []))


def _decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _strip_html(markup: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _attachment_parts(part: dict) -> list[dict]:
    """Return the file attachments of a message, numbered from 1 in the order Gmail lists them."""
    found: list[dict] = []
    _find_attachments(part, found)
    return [{"index": i, **attachment} for i, attachment in enumerate(found, start=1)]


def _find_attachments(part: dict, found: list[dict]) -> None:
    body = part.get("body", {})
    if part.get("filename") and body.get("attachmentId"):
        found.append({
            "attachment_id": body["attachmentId"],
            "filename": part["filename"],
            "mime_type": part.get("mimeType", "application/octet-stream"),
            "size": body.get("size", 0),
        })
    for child in part.get("parts", []):
        _find_attachments(child, found)


def _headers(message: dict) -> dict[str, str]:
    return {header["name"].lower(): header["value"] for header in message.get("payload", {}).get("headers", [])}
