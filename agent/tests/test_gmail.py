import base64

import pytest

from gmail_assistant.gmail import RESERVED_LABELS, GmailClient, _attachment_parts, extract_text
from gmail_assistant.telegram import build_multipart


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def test_extract_text_prefers_plain_part_in_multipart():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("Hello plain")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>Hello html</p>")}},
        ],
    }
    assert extract_text(payload) == "Hello plain"


def test_extract_text_falls_back_to_stripped_html():
    payload = {
        "mimeType": "text/html",
        "body": {"data": _b64("<style>p{}</style><p>Sconto&nbsp;50%</p><script>x()</script>")},
    }
    assert extract_text(payload) == "Sconto 50%"


def test_extract_text_handles_unpadded_base64():
    # Gmail returns URL-safe base64 without padding.
    data = base64.urlsafe_b64encode(b"ab").decode().rstrip("=")
    assert extract_text({"mimeType": "text/plain", "body": {"data": data}}) == "ab"


def test_move_to_folder_refuses_system_labels():
    client = GmailClient(service=None)
    for label in RESERVED_LABELS:
        with pytest.raises(ValueError):
            client.move_to_folder(["abc"], label.lower())


def test_attachment_parts_are_found_and_numbered():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("ciao")}},
            {
                "mimeType": "multipart/related",
                "parts": [{"mimeType": "image/png", "filename": "logo.png", "body": {"size": 10}}],
            },
            {
                "mimeType": "application/pdf",
                "filename": "referto.pdf",
                "body": {"attachmentId": "abc123", "size": 2048},
            },
        ],
    }
    assert _attachment_parts(payload) == [
        {"index": 1, "attachment_id": "abc123", "filename": "referto.pdf", "mime_type": "application/pdf", "size": 2048}
    ]


def test_multipart_contains_chat_id_and_file():
    body, content_type = build_multipart({"chat_id": "42"}, "document", "referto.pdf", b"%PDF", "application/pdf")
    boundary = content_type.split("boundary=")[1].encode()
    assert content_type.startswith("multipart/form-data")
    assert b'name="chat_id"\r\n\r\n42' in body
    assert b'name="document"; filename="referto.pdf"' in body
    assert body.endswith(b"--" + boundary + b"--\r\n")
    assert b"%PDF" in body
