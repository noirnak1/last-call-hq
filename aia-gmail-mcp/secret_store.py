"""Thin wrapper over Secret Manager.

Refresh tokens and the client secret never appear in code, in the repo, or in
chat. They are written once (by the /setup flow, or by you in Cloud Shell) and
read at runtime by the service account.
"""
import functools

from google.cloud import secretmanager

import config

_client = None


def client() -> secretmanager.SecretManagerServiceClient:
    global _client
    if _client is None:
        _client = secretmanager.SecretManagerServiceClient()
    return _client


def _parent() -> str:
    return f"projects/{config.PROJECT_ID}"


def read(name: str) -> str | None:
    path = f"{_parent()}/secrets/{name}/versions/latest"
    try:
        resp = client().access_secret_version(request={"name": path})
    except Exception:
        return None
    return resp.payload.data.decode("utf-8").strip()


def write(name: str, value: str) -> None:
    """Create the secret if needed, then add a new version."""
    try:
        client().create_secret(
            request={
                "parent": _parent(),
                "secret_id": name,
                "secret": {"replication": {"automatic": {}}},
            }
        )
    except Exception:
        pass  # already exists
    client().add_secret_version(
        request={
            "parent": f"{_parent()}/secrets/{name}",
            "payload": {"data": value.encode("utf-8")},
        }
    )


@functools.lru_cache(maxsize=1)
def client_secret() -> str:
    value = read(config.CLIENT_SECRET_NAME)
    if not value:
        raise RuntimeError(
            f"Secret {config.CLIENT_SECRET_NAME} is empty or missing. "
            "Put the OAuth client secret there before deploying."
        )
    return value


@functools.lru_cache(maxsize=1)
def signing_key() -> str:
    """Key used to sign the tokens we hand to Claude."""
    value = read(config.SIGNING_KEY_NAME)
    if not value:
        raise RuntimeError(
            f"Secret {config.SIGNING_KEY_NAME} is missing. Create it with a long "
            "random string before deploying."
        )
    return value


def refresh_token(alias: str) -> str | None:
    return read(f"{config.REFRESH_TOKEN_PREFIX}{alias}")


def save_refresh_token(alias: str, token: str) -> None:
    write(f"{config.REFRESH_TOKEN_PREFIX}{alias}", token)
