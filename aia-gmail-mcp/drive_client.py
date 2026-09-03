"""Drive access, read-only, across every connected account.

Google Docs, Sheets and Slides are exported as plain text so the model can read
them. Other text-like files are downloaded directly. Binary files return
metadata only.
"""
from google_auth import AccountNotConnected, call, call_raw, each_connected, resolve

BASE = "https://www.googleapis.com/drive/v3"
FIELDS = "files(id,name,mimeType,modifiedTime,size,webViewLink,owners(emailAddress))"

EXPORT_AS_TEXT = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
TEXT_LIKE_PREFIXES = ("text/", "application/json", "application/xml")


def _shape(alias: str, f: dict) -> dict:
    owners = [o.get("emailAddress") for o in f.get("owners", []) or []]
    return {
        "account": alias,
        "id": f.get("id"),
        "name": f.get("name"),
        "type": f.get("mimeType"),
        "modified": f.get("modifiedTime"),
        "size": f.get("size"),
        "owner": owners[0] if owners else "",
        "link": f.get("webViewLink"),
    }


def search(alias: str, query: str, limit: int = 15) -> list[dict]:
    """Search by name and full text. Plain words work; Drive's own query syntax
    also works if you pass it, e.g. "name contains 'pitch' and mimeType contains 'pdf'".
    """
    alias = resolve(alias)
    q = query.strip()
    looks_like_drive_syntax = any(
        tok in q for tok in ("contains", "mimeType", "modifiedTime", " = ", "in parents")
    )
    if not looks_like_drive_syntax:
        safe = q.replace("\\", "\\\\").replace("'", "\\'")
        q = f"(name contains '{safe}' or fullText contains '{safe}') and trashed = false"
    data = call(
        alias,
        "GET",
        f"{BASE}/files",
        params={
            "q": q,
            "fields": FIELDS,
            "orderBy": "modifiedTime desc",
            "pageSize": min(max(limit, 1), 50),
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        },
    )
    return [_shape(alias, f) for f in data.get("files", [])]


def search_all(query: str, limit_per_account: int = 10) -> list[dict]:
    out = []
    for alias in each_connected():
        try:
            out.extend(search(alias, query, limit_per_account))
        except AccountNotConnected:
            continue
    out.sort(key=lambda f: f.get("modified") or "", reverse=True)
    return out


def recent(alias: str, limit: int = 20) -> list[dict]:
    alias = resolve(alias)
    data = call(
        alias,
        "GET",
        f"{BASE}/files",
        params={
            "q": "trashed = false",
            "fields": FIELDS,
            "orderBy": "modifiedTime desc",
            "pageSize": min(max(limit, 1), 50),
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        },
    )
    return [_shape(alias, f) for f in data.get("files", [])]


def read_file(alias: str, file_id: str, max_chars: int = 30000) -> dict:
    alias = resolve(alias)
    meta = call(
        alias,
        "GET",
        f"{BASE}/files/{file_id}",
        params={
            "fields": "id,name,mimeType,modifiedTime,size,webViewLink",
            "supportsAllDrives": "true",
        },
    )
    mime = meta.get("mimeType", "")
    if mime in EXPORT_AS_TEXT:
        text, _ = call_raw(
            alias,
            f"{BASE}/files/{file_id}/export",
            params={"mimeType": EXPORT_AS_TEXT[mime]},
        )
    elif mime.startswith(TEXT_LIKE_PREFIXES):
        text, _ = call_raw(
            alias,
            f"{BASE}/files/{file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
        )
    else:
        return {
            "account": alias,
            "id": file_id,
            "name": meta.get("name"),
            "type": mime,
            "link": meta.get("webViewLink"),
            "content": None,
            "note": "Binary file. Open the link to view it.",
        }
    truncated = len(text) > max_chars
    return {
        "account": alias,
        "id": file_id,
        "name": meta.get("name"),
        "type": mime,
        "modified": meta.get("modifiedTime"),
        "link": meta.get("webViewLink"),
        "content": text[:max_chars],
        "truncated": truncated,
    }
