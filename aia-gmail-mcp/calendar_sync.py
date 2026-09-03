"""One-way calendar mirror.

Every event on the source accounts, in a rolling window, is copied into the
target account's primary calendar. Idempotent: each mirrored event carries a
private key naming its source, so reruns update rather than duplicate, and
events that vanish from the source are removed from the target.

Skips: cancelled events, events that are themselves mirrors, and events the
target account is already invited to (same iCalUID), so nothing doubles.
"""
import time
from datetime import datetime, timedelta, timezone

import config
from google_auth import AccountNotConnected, call, resolve

BASE = "https://www.googleapis.com/calendar/v3"
KEY = "aia_mirror_key"
LABELS = {"miami": "[Miami]", "personal": "[Personal]", "aia": "[AIA]"}

_last_run: dict = {"at": None, "ok": None, "summary": None}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _window() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return (
        _iso(now - timedelta(days=config.MIRROR_DAYS_BACK)),
        _iso(now + timedelta(days=config.MIRROR_DAYS_AHEAD)),
    )


def _list(alias: str, extra: dict | None = None) -> list[dict]:
    """All primary-calendar instances in the window, paging through."""
    t_min, t_max = _window()
    out, token = [], None
    while True:
        params = {
            "timeMin": t_min,
            "timeMax": t_max,
            "singleEvents": "true",
            "maxResults": 250,
            "showDeleted": "false",
        }
        if extra:
            params.update(extra)
        if token:
            params["pageToken"] = token
        data = call(alias, "GET", f"{BASE}/calendars/primary/events", params=params)
        out.extend(data.get("items", []))
        token = data.get("nextPageToken")
        if not token:
            break
    return out


def _label(alias: str) -> str:
    return LABELS.get(alias, f"[{alias.title()}]")


def _mirror_body(source_alias: str, ev: dict) -> dict:
    key = f"{source_alias}:{ev['id']}"
    title = ev.get("summary") or "(no title)"
    label = _label(source_alias)
    if not title.startswith(label):
        title = f"{label} {title}"
    body = {
        "summary": title,
        "start": ev.get("start"),
        "end": ev.get("end"),
        "location": ev.get("location", ""),
        "description": (ev.get("description") or "")[:8000],
        "visibility": "private",
        "transparency": ev.get("transparency", "opaque"),
        "reminders": {"useDefault": False, "overrides": []},
        "extendedProperties": {
            "private": {
                KEY: key,
                "aia_mirror_source": source_alias,
                "aia_mirror_ical": ev.get("iCalUID", ""),
            }
        },
    }
    link = ev.get("htmlLink")
    if link:
        body["source"] = {"title": f"Original on {source_alias}", "url": link}
    return body


def _same_content(existing: dict, body: dict) -> bool:
    for k in ("summary", "start", "end", "location", "description"):
        if (existing.get(k) or "") != (body.get(k) or ""):
            return False
    return True


def run() -> dict:
    """Mirror once. Returns a summary. Safe to call every hour."""
    target = resolve(config.MIRROR_TARGET)
    started = time.time()
    summary = {
        "target": target,
        "sources": [],
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "skipped_invited_on_both": 0,
        "unchanged": 0,
        "errors": [],
    }

    # What the target already has: mirrored events keyed by our marker, and
    # every iCalUID so invitations that reached both accounts are not doubled.
    target_events = _list(target)
    existing_mirrors: dict[str, dict] = {}
    target_icals: set[str] = set()
    for ev in target_events:
        props = (ev.get("extendedProperties") or {}).get("private") or {}
        if KEY in props:
            existing_mirrors[props[KEY]] = ev
        elif ev.get("iCalUID"):
            target_icals.add(ev["iCalUID"])

    seen: set[str] = set()
    for src in config.mirror_sources():
        try:
            events = _list(src)
        except AccountNotConnected as exc:
            summary["errors"].append(f"{src}: {exc}")
            continue
        except RuntimeError as exc:
            summary["errors"].append(f"{src}: {exc}")
            continue
        summary["sources"].append({"account": src, "events_in_window": len(events)})

        for ev in events:
            if ev.get("status") == "cancelled":
                continue
            props = (ev.get("extendedProperties") or {}).get("private") or {}
            if KEY in props:
                continue  # never mirror a mirror
            if ev.get("iCalUID") and ev["iCalUID"] in target_icals:
                summary["skipped_invited_on_both"] += 1
                continue

            body = _mirror_body(src, ev)
            key = body["extendedProperties"]["private"][KEY]
            seen.add(key)
            try:
                if key in existing_mirrors:
                    cur = existing_mirrors[key]
                    if _same_content(cur, body):
                        summary["unchanged"] += 1
                    else:
                        call(target, "PATCH", f"{BASE}/calendars/primary/events/{cur['id']}", json=body)
                        summary["updated"] += 1
                else:
                    call(target, "POST", f"{BASE}/calendars/primary/events", json=body)
                    summary["created"] += 1
            except RuntimeError as exc:
                summary["errors"].append(f"{key}: {str(exc)[:160]}")

    # Anything we mirrored before that no longer exists at the source.
    for key, ev in existing_mirrors.items():
        if key not in seen:
            try:
                call(target, "DELETE", f"{BASE}/calendars/primary/events/{ev['id']}")
                summary["deleted"] += 1
            except RuntimeError as exc:
                summary["errors"].append(f"delete {key}: {str(exc)[:160]}")

    summary["seconds"] = round(time.time() - started, 1)
    _last_run.update({"at": _iso(datetime.now(timezone.utc)), "ok": not summary["errors"], "summary": summary})
    return summary


def status() -> dict:
    return {
        "target": config.MIRROR_TARGET,
        "sources": config.mirror_sources(),
        "window_days": {"back": config.MIRROR_DAYS_BACK, "ahead": config.MIRROR_DAYS_AHEAD},
        "last_run": _last_run,
        "note": "last_run is per-instance memory; Cloud Run may show None after a cold start even if the hourly job is healthy. Check Cloud Scheduler for the authoritative history.",
    }
