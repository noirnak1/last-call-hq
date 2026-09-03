"""Gmail access for each configured mailbox.

One stored refresh token per account. Access tokens are minted on demand and
cached in memory for the life of the instance.
"""
import base64
from email.message import EmailMessage

import httpx

import config
import secret_store

from google_auth import AccountNotConnected, access_token as _access_token, resolve as _resolve  # noqa: E402


def _call(alias: str, method: str, path: str, **kwargs) -> dict:
    token = _access_token(alias)
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    resp = httpx.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        **kwargs,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Gmail API {resp.status_code}: {resp.text[:400]}")
    return resp.json() if resp.text else {}


def list_accounts() -> list[dict]:
    out = []
    for alias, email in config.accounts().items():
        out.append(
            {
                "account": alias,
                "email": email,
                "connected": secret_store.refresh_token(alias) is not None,
            }
        )
    return out


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def search(alias: str, query: str, limit: int = 15) -> list[dict]:
    alias = _resolve(alias)
    data = _call(
        alias,
        "GET",
        "messages",
        params={"q": query, "maxResults": min(max(limit, 1), 50)},
    )
    results = []
    for stub in data.get("messages", []):
        msg = _call(
            alias,
            "GET",
            f"messages/{stub['id']}",
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "To", "Subject", "Date"],
            },
        )
        headers = msg.get("payload", {}).get("headers", [])
        results.append(
            {
                "account": alias,
                "id": msg.get("id"),
                "thread_id": msg.get("threadId"),
                "from": _header(headers, "From"),
                "to": _header(headers, "To"),
                "subject": _header(headers, "Subject"),
                "date": _header(headers, "Date"),
                "snippet": msg.get("snippet", ""),
                "unread": "UNREAD" in msg.get("labelIds", []),
            }
        )
    return results


def search_all(query: str, limit: int = 10) -> list[dict]:
    out = []
    for alias in config.accounts():
        try:
            out.extend(search(alias, query, limit))
        except AccountNotConnected:
            continue
    out.sort(key=lambda m: m.get("date", ""), reverse=True)
    return out


def _decode_part(part: dict) -> str:
    body = part.get("body", {})
    data = body.get("data")
    if data:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
    return ""


def _walk_body(payload: dict) -> str:
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        return _decode_part(payload)
    chunks = []
    for part in payload.get("parts", []) or []:
        chunks.append(_walk_body(part))
    text = "\n".join(c for c in chunks if c).strip()
    if text:
        return text
    if mime == "text/html":
        return _decode_part(payload)
    return ""


def read_thread(alias: str, thread_id: str, max_chars: int = 20000) -> dict:
    alias = _resolve(alias)
    data = _call(alias, "GET", f"threads/{thread_id}", params={"format": "full"})
    messages = []
    for msg in data.get("messages", []):
        headers = msg.get("payload", {}).get("headers", [])
        body = _walk_body(msg.get("payload", {}))
        messages.append(
            {
                "id": msg.get("id"),
                "from": _header(headers, "From"),
                "to": _header(headers, "To"),
                "cc": _header(headers, "Cc"),
                "date": _header(headers, "Date"),
                "subject": _header(headers, "Subject"),
                "body": body[:max_chars],
            }
        )
    return {"account": alias, "thread_id": thread_id, "messages": messages}


def create_draft(
    alias: str,
    to: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
    cc: str | None = None,
) -> dict:
    """Creates a draft. Never sends. Sending stays a human action in Gmail."""
    alias = _resolve(alias)
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["From"] = config.accounts()[alias]
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload: dict = {"message": {"raw": raw}}
    if thread_id:
        payload["message"]["threadId"] = thread_id
    data = _call(alias, "POST", "drafts", json=payload)
    return {
        "account": alias,
        "draft_id": data.get("id"),
        "status": "draft created, not sent",
    }


def unread_summary(limit_per_account: int = 10) -> list[dict]:
    return search_all("is:unread in:inbox", limit_per_account)
