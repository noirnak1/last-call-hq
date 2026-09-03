"""Google Chat access, read-only, across every connected account.

The Chat API has no full-text search for users, so search here means pulling
recent messages from every space and matching text on our side. Fine for the
"what did Brent say about the Teams channel" case, which is the real use.
"""
from datetime import datetime, timedelta, timezone

from google_auth import AccountNotConnected, call, each_connected, resolve

BASE = "https://chat.googleapis.com/v1"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def spaces(alias: str, limit: int = 100) -> list[dict]:
    alias = resolve(alias)
    out, token = [], None
    while True:
        params = {"pageSize": min(limit, 100)}
        if token:
            params["pageToken"] = token
        data = call(alias, "GET", f"{BASE}/spaces", params=params)
        for s in data.get("spaces", []):
            out.append(
                {
                    "account": alias,
                    "space": s.get("name"),
                    "display_name": s.get("displayName") or "(direct message)",
                    "type": s.get("spaceType", ""),
                    "last_active": s.get("lastActiveTime", ""),
                }
            )
        token = data.get("nextPageToken")
        if not token or len(out) >= limit:
            break
    out.sort(key=lambda s: s.get("last_active") or "", reverse=True)
    return out[:limit]


def _shape(alias: str, space_label: str, m: dict) -> dict:
    sender = m.get("sender", {}) or {}
    return {
        "account": alias,
        "space": m.get("space", {}).get("name", ""),
        "space_name": space_label,
        "id": m.get("name"),
        "thread": (m.get("thread") or {}).get("name", ""),
        "sender": sender.get("displayName") or sender.get("name", ""),
        "sender_email": (sender.get("email") or ""),
        "time": m.get("createTime", ""),
        "text": (m.get("text") or m.get("formattedText") or "")[:2000],
        "has_attachment": bool(m.get("attachment")),
    }


def messages(alias: str, space: str, days_back: int = 7, limit: int = 50) -> list[dict]:
    alias = resolve(alias)
    since = _iso(datetime.now(timezone.utc) - timedelta(days=days_back))
    data = call(
        alias,
        "GET",
        f"{BASE}/{space}/messages",
        params={
            "pageSize": min(max(limit, 1), 100),
            "orderBy": "createTime desc",
            "filter": f'createTime > "{since}"',
        },
    )
    label = data.get("messages", [{}])[0].get("space", {}).get("displayName", "") if data.get("messages") else ""
    return [_shape(alias, label, m) for m in data.get("messages", [])]


def recent_all(days_back: int = 1, limit_per_space: int = 15, max_spaces: int = 25) -> list[dict]:
    """Newest messages across every space of every connected account."""
    out = []
    for alias in each_connected():
        try:
            for s in spaces(alias, limit=max_spaces):
                try:
                    msgs = messages(alias, s["space"], days_back, limit_per_space)
                    for m in msgs:
                        m["space_name"] = s["display_name"]
                    out.extend(msgs)
                except RuntimeError:
                    continue
        except (AccountNotConnected, RuntimeError):
            continue
    out.sort(key=lambda m: m.get("time") or "", reverse=True)
    return out


def search_all(query: str, days_back: int = 14, limit_per_space: int = 50, max_spaces: int = 40) -> list[dict]:
    """Case-insensitive text match over recent messages in every space."""
    needle = query.strip().lower()
    hits = []
    for m in recent_all(days_back, limit_per_space, max_spaces):
        hay = f"{m.get('text','')} {m.get('sender','')} {m.get('space_name','')}".lower()
        if needle in hay:
            hits.append(m)
    return hits
