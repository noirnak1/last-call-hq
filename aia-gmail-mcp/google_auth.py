"""Shared Google auth for every API client.

One stored refresh token per account. Access tokens are minted on demand and
cached in memory for the life of the instance. Gmail, Calendar and Drive all
ride on the same token, which carries whatever scopes were granted at /setup.
"""
import time

import httpx

import config
import secret_store

_access_cache: dict[str, tuple[str, float]] = {}


class AccountNotConnected(Exception):
    pass


def resolve(alias: str) -> str:
    alias = (alias or "").strip().lower()
    known = config.accounts()
    if alias in known:
        return alias
    for a, email in known.items():
        if email == alias:
            return a
    raise ValueError(
        f"Unknown account '{alias}'. Configured accounts: {', '.join(known)}"
    )


def access_token(alias: str) -> str:
    cached = _access_cache.get(alias)
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    refresh = secret_store.refresh_token(alias)
    if not refresh:
        raise AccountNotConnected(
            f"Account '{alias}' has not been connected yet. "
            f"Visit {config.BASE_URL}/setup to authorize it."
        )

    resp = httpx.post(
        config.GOOGLE_TOKEN_URL,
        data={
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": secret_store.client_secret(),
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload["access_token"]
    _access_cache[alias] = (token, time.time() + payload.get("expires_in", 3600))
    return token


def call(alias: str, method: str, url: str, **kwargs) -> dict:
    """Authenticated request against any Google API. Returns parsed JSON."""
    token = access_token(alias)
    resp = httpx.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        **kwargs,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Google API {resp.status_code}: {resp.text[:400]}")
    return resp.json() if resp.text else {}


def call_raw(alias: str, url: str, **kwargs) -> tuple[str, str]:
    """Authenticated GET returning (text, content_type). For file bodies."""
    token = access_token(alias)
    resp = httpx.get(
        url, headers={"Authorization": f"Bearer {token}"}, timeout=60, **kwargs
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Google API {resp.status_code}: {resp.text[:400]}")
    return resp.text, resp.headers.get("content-type", "")


def each_connected():
    """Yield the alias of every account that has a refresh token."""
    for alias in config.accounts():
        if secret_store.refresh_token(alias) is not None:
            yield alias
