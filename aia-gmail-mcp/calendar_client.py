"""Calendar access, read-only, across every connected account."""
from datetime import datetime, timedelta, timezone

from google_auth import AccountNotConnected, call, each_connected, resolve

BASE = "https://www.googleapis.com/calendar/v3"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _shape(alias: str, ev: dict) -> dict:
    start = ev.get("start", {})
    end = ev.get("end", {})
    attendees = [
        a.get("email")
        for a in ev.get("attendees", []) or []
        if a.get("email")
    ]
    return {
        "account": alias,
        "id": ev.get("id"),
        "title": ev.get("summary", "(no title)"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "all_day": "date" in start,
        "location": ev.get("location", ""),
        "organizer": (ev.get("organizer") or {}).get("email", ""),
        "attendees": attendees,
        "meet_link": ev.get("hangoutLink", ""),
        "status": ev.get("status", ""),
        "description": (ev.get("description") or "")[:600],
        "link": ev.get("htmlLink", ""),
    }


def events(
    alias: str,
    days_ahead: int = 7,
    days_back: int = 0,
    query: str | None = None,
    limit: int = 25,
) -> list[dict]:
    """Events on the primary calendar in a window around now."""
    alias = resolve(alias)
    now = datetime.now(timezone.utc)
    params = {
        "timeMin": _iso(now - timedelta(days=days_back)),
        "timeMax": _iso(now + timedelta(days=days_ahead)),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": min(max(limit, 1), 100),
    }
    if query:
        params["q"] = query
    data = call(alias, "GET", f"{BASE}/calendars/primary/events", params=params)
    return [_shape(alias, ev) for ev in data.get("items", [])]


def events_all(
    days_ahead: int = 7,
    days_back: int = 0,
    query: str | None = None,
    limit_per_account: int = 25,
) -> list[dict]:
    """Same window across every connected account, merged and sorted."""
    out = []
    for alias in each_connected():
        try:
            out.extend(events(alias, days_ahead, days_back, query, limit_per_account))
        except AccountNotConnected:
            continue
    out.sort(key=lambda e: e.get("start") or "")
    return out
