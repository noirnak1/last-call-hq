"""Configuration. Everything comes from env vars set on the Cloud Run service.

Nothing secret is ever committed to this repo. The Google client secret and the
per-account refresh tokens live in Secret Manager and are read at runtime.
"""
import os

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "aia-mcp")

# The Web application OAuth client from APIs & Services > Credentials.
GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID",
    "805857880378-jdk6s7kf1j7fj06b455ik2o4t74mcc8k.apps.googleusercontent.com",
)

# Secret Manager secret names.
CLIENT_SECRET_NAME = os.environ.get("CLIENT_SECRET_NAME", "google-oauth-client-secret")
SIGNING_KEY_NAME = os.environ.get("SIGNING_KEY_NAME", "mcp-token-signing-key")
REFRESH_TOKEN_PREFIX = os.environ.get("REFRESH_TOKEN_PREFIX", "gmail-refresh-")

# Public base URL of this service, e.g. https://aia-gmail-mcp-xxxx.us-central1.run.app
# Cloud Run does not tell the container its own URL, so we set it after first deploy.
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

# Which mailboxes this server serves. Comma separated "alias:email" pairs.
# Alias is what you say to Claude ("search aia for..."), email is the real address.
ACCOUNTS_RAW = os.environ.get(
    "ACCOUNTS",
    "aia:noah@aianswered.com,"
    "personal:noahkruthaupt@gmail.com,"
    "miami:kruthana@miamioh.edu",
)


def accounts() -> dict[str, str]:
    out = {}
    for pair in ACCOUNTS_RAW.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        alias, email = pair.split(":", 1)
        out[alias.strip().lower()] = email.strip().lower()
    return out


# Only these Google identities may complete the Claude sign-in. Anyone else who
# somehow reaches the URL gets refused before a token is ever issued.
def allowed_emails() -> set[str]:
    extra = os.environ.get("EXTRA_ALLOWED_EMAILS", "")
    emails = set(accounts().values())
    emails.update(e.strip().lower() for e in extra.split(",") if e.strip())
    return emails


# Gmail read plus draft, Calendar read, Drive read, Chat read. Deliberately no gmail.send: the tool layer exposes drafting
# only. To enable sending, add "https://www.googleapis.com/auth/gmail.send" here,
# re-run /setup for each account, and add a send tool in mcp_tools.py.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

TOKEN_TTL_SECONDS = 60 * 60 * 12
CODE_TTL_SECONDS = 300

# ---------------------------------------------------------------- calendar mirror
# Events from every other account are copied into MIRROR_TARGET's primary
# calendar. Only the target needs write access, so only it is asked for
# WRITE_SCOPE at /setup. Sources stay read-only.
MIRROR_TARGET = os.environ.get("MIRROR_TARGET", "aia").strip().lower()
MIRROR_DAYS_BACK = int(os.environ.get("MIRROR_DAYS_BACK", "1"))
MIRROR_DAYS_AHEAD = int(os.environ.get("MIRROR_DAYS_AHEAD", "60"))
WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
SYNC_TOKEN_NAME = os.environ.get("SYNC_TOKEN_NAME", "calendar-sync-token")


def mirror_sources() -> list[str]:
    return [a for a in accounts() if a != MIRROR_TARGET]


def scopes_for(alias: str) -> list[str]:
    """Base scopes, plus calendar write only for the mirror target."""
    out = list(SCOPES)
    if alias == MIRROR_TARGET and WRITE_SCOPE not in out:
        out.insert(3, WRITE_SCOPE)
    return out
