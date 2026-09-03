"""aia-gmail-mcp

A remote MCP server that reaches several Gmail accounts at once.

It wears two hats:

  To Google, it is the Web application OAuth client in the aia-mcp project.
  To Claude, it is an OAuth authorization server. Claude sends you to Google to
  prove who you are, and gets back a token minted here.

Signing in with Google only proves identity. The mailboxes themselves are
reached with refresh tokens stored in Secret Manager, put there once by /setup.
"""
import base64
import hashlib
import json
import secrets
import time
import urllib.parse

import httpx
import jwt
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import config
import mcp_tools
import secret_store

app = FastAPI(title="aia-gmail-mcp")


def base_url(request: Request) -> str:
    if config.BASE_URL:
        return config.BASE_URL
    return str(request.base_url).rstrip("/")


def _sign(payload: dict, ttl: int) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl
    body["iat"] = int(time.time())
    return jwt.encode(body, secret_store.signing_key(), algorithm="HS256")


def _verify(token: str) -> dict | None:
    try:
        return jwt.decode(token, secret_store.signing_key(), algorithms=["HS256"])
    except Exception:
        return None


# ---------------------------------------------------------------- discovery


@app.get("/.well-known/oauth-protected-resource")
@app.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource(request: Request):
    base = base_url(request)
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
    }


@app.get("/.well-known/oauth-authorization-server")
@app.get("/.well-known/oauth-authorization-server/mcp")
def auth_server_metadata(request: Request):
    base = base_url(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": ["mcp"],
    }


@app.post("/register")
async def register(request: Request):
    """Dynamic client registration. Claude registers itself here.

    We are a single-tenant server behind an email allowlist, so we accept the
    registration and hand back an identifier. The real gate is the Google
    sign-in and the allowlist check in /oauth/callback.
    """
    body = await request.json()
    redirect_uris = body.get("redirect_uris") or []
    client_id = "claude-" + secrets.token_urlsafe(16)
    return JSONResponse(
        {
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        status_code=201,
    )


# ---------------------------------------------------------------- claude auth


@app.get("/authorize")
def authorize(request: Request):
    """Claude starts here. We bounce the human to Google to prove identity."""
    q = request.query_params
    state_payload = {
        "kind": "claude_auth",
        "redirect_uri": q.get("redirect_uri", ""),
        "state": q.get("state", ""),
        "code_challenge": q.get("code_challenge", ""),
        "code_challenge_method": q.get("code_challenge_method", "S256"),
        "client_id": q.get("client_id", ""),
    }
    state = _sign(state_payload, config.CODE_TTL_SECONDS)

    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{base_url(request)}/oauth/callback",
        "response_type": "code",
        "scope": "openid https://www.googleapis.com/auth/userinfo.email",
        "state": state,
        "prompt": "select_account",
    }
    return RedirectResponse(
        f"{config.GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}", status_code=302
    )


@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    """Google returns here for both the Claude sign-in and the /setup flow."""
    code = request.query_params.get("code")
    state = _verify(request.query_params.get("state", "") or "")
    if not code or not state:
        return HTMLResponse("<h3>Sign-in expired. Start again.</h3>", status_code=400)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            config.GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": secret_store.client_secret(),
                "redirect_uri": f"{base_url(request)}/oauth/callback",
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code >= 400:
            return HTMLResponse(f"<h3>Google refused: {resp.text}</h3>", status_code=400)
        tokens = resp.json()

        info = await client.get(
            config.GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        email = (info.json().get("email") or "").lower()

    if email not in config.allowed_emails():
        return HTMLResponse(
            f"<h3>{email} is not on the allowlist for this server.</h3>", status_code=403
        )

    if state.get("kind") == "setup":
        alias = state["alias"]
        expected = config.accounts().get(alias)
        if email != expected:
            return HTMLResponse(
                f"<h3>You signed in as {email} but this step wanted {expected}. "
                "Go back and pick the right account.</h3>",
                status_code=400,
            )
        refresh = tokens.get("refresh_token")
        if not refresh:
            return HTMLResponse(
                "<h3>Google did not return a refresh token. Revoke this app at "
                "myaccount.google.com > Security > Third-party access, then retry.</h3>",
                status_code=400,
            )
        secret_store.save_refresh_token(alias, refresh)
        return HTMLResponse(
            f"<h3>{email} connected.</h3><p><a href='/setup'>Back to setup</a></p>"
        )

    # Claude sign-in: mint our own authorization code and hand it back.
    auth_code = _sign(
        {
            "kind": "code",
            "sub": email,
            "code_challenge": state.get("code_challenge", ""),
        },
        config.CODE_TTL_SECONDS,
    )
    sep = "&" if "?" in state["redirect_uri"] else "?"
    target = (
        f"{state['redirect_uri']}{sep}"
        f"code={urllib.parse.quote(auth_code)}&state={urllib.parse.quote(state['state'])}"
    )
    return RedirectResponse(target, status_code=302)


@app.post("/token")
async def token(request: Request):
    form = dict(await request.form())
    grant = form.get("grant_type")

    if grant == "refresh_token":
        claims = _verify(form.get("refresh_token", ""))
        if not claims or claims.get("kind") != "refresh":
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        sub = claims["sub"]
    elif grant == "authorization_code":
        claims = _verify(form.get("code", ""))
        if not claims or claims.get("kind") != "code":
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        challenge = claims.get("code_challenge")
        if challenge:
            verifier = form.get("code_verifier", "")
            digest = hashlib.sha256(verifier.encode()).digest()
            computed = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            if computed != challenge:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
        sub = claims["sub"]
    else:
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    return {
        "access_token": _sign({"kind": "access", "sub": sub}, config.TOKEN_TTL_SECONDS),
        "refresh_token": _sign({"kind": "refresh", "sub": sub}, 60 * 60 * 24 * 30),
        "token_type": "Bearer",
        "expires_in": config.TOKEN_TTL_SECONDS,
    }


# ---------------------------------------------------------------- mcp


def _bearer(request: Request) -> dict | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    claims = _verify(header[7:])
    if not claims or claims.get("kind") != "access":
        return None
    return claims


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    if _bearer(request) is None:
        base = base_url(request)
        return JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    'Bearer resource_metadata='
                    f'"{base}/.well-known/oauth-protected-resource"'
                )
            },
        )

    body = await request.json()
    if isinstance(body, list):
        out = [r for r in (mcp_tools.handle_rpc(m) for m in body) if r is not None]
        return JSONResponse(out if out else None, status_code=200 if out else 202)

    result = mcp_tools.handle_rpc(body)
    if result is None:
        return JSONResponse(None, status_code=202)
    return JSONResponse(result)


@app.get("/mcp")
async def mcp_get(request: Request):
    if _bearer(request) is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({"error": "SSE stream not supported"}, status_code=405)


# ---------------------------------------------------------------- setup


@app.get("/setup", response_class=HTMLResponse)
def setup(request: Request):
    """One-time page. Authorize each mailbox so the server can read it later."""
    rows = []
    for alias, email in config.accounts().items():
        connected = secret_store.refresh_token(alias) is not None
        mark = "connected" if connected else "not connected"
        rows.append(
            f"<li><b>{alias}</b> ({email}) &mdash; {mark} "
            f"&nbsp;<a href='/setup/start?alias={alias}'>"
            f"{'reconnect' if connected else 'connect'}</a></li>"
        )
    return f"""
    <html><body style="font-family:system-ui;max-width:640px;margin:60px auto">
    <h2>aia-gmail-mcp setup</h2>
    <p>Authorize each mailbox once. Refresh tokens go straight into Secret
    Manager. You can revoke any of them at
    <a href="https://myaccount.google.com/permissions">myaccount.google.com</a>.</p>
    <ul>{''.join(rows)}</ul>
    </body></html>
    """


@app.get("/setup/start")
def setup_start(request: Request, alias: str):
    if alias not in config.accounts():
        return HTMLResponse("<h3>Unknown account alias.</h3>", status_code=400)
    state = _sign({"kind": "setup", "alias": alias}, config.CODE_TTL_SECONDS)
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{base_url(request)}/oauth/callback",
        "response_type": "code",
        "scope": " ".join(config.SCOPES),
        "access_type": "offline",
        "prompt": "consent select_account",
        "login_hint": config.accounts()[alias],
        "state": state,
    }
    return RedirectResponse(
        f"{config.GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}", status_code=302
    )


@app.get("/healthz")
def healthz():
    return {"ok": True, "accounts": [a["account"] for a in _safe_accounts()]}


def _safe_accounts():
    try:
        import gmail_client

        return gmail_client.list_accounts()
    except Exception:
        return []
