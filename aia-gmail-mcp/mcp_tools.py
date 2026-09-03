"""MCP tool definitions and dispatch.

Gmail read and draft, Calendar read, Drive read, Chat read. There is no send tool by design: the July build was
read-only on purpose, and drafting is the useful half of the upgrade without
handing an agent the ability to mail your network unprompted.
"""
import json

import calendar_client
import calendar_sync
import chat_client
import config
import drive_client
import gmail_client

SERVER_INFO = {"name": "aia-google", "version": "1.3.0"}
PROTOCOL_VERSION = "2025-06-18"

_ACCOUNT_DESC = (
    "Which mailbox. One of: "
    + ", ".join(f"{a} ({e})" for a, e in config.accounts().items())
)

TOOLS = [
    {
        "name": "list_accounts",
        "description": (
            "List the mailboxes this server can reach and whether each one is "
            "authorized. Call this first if you are unsure which accounts exist."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_all_inboxes",
        "description": (
            "Search every connected mailbox at once with a Gmail query and return "
            "matches newest first. This is the tool that the built-in single-account "
            "Gmail connector cannot do. Use Gmail search syntax, e.g. "
            "'from:cameron newer_than:14d', 'is:unread in:inbox', "
            "'subject:invoice has:attachment'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query."},
                "limit": {
                    "type": "integer",
                    "description": "Max results per mailbox. Default 10.",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_inbox",
        "description": "Search one specific mailbox with a Gmail query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": _ACCOUNT_DESC},
                "query": {"type": "string", "description": "Gmail search query."},
                "limit": {"type": "integer", "default": 15},
            },
            "required": ["account", "query"],
        },
    },
    {
        "name": "read_thread",
        "description": (
            "Read the full text of every message in one thread. Use after a search "
            "when you need what was actually said rather than the snippet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": _ACCOUNT_DESC},
                "thread_id": {
                    "type": "string",
                    "description": "thread_id from a search result.",
                },
            },
            "required": ["account", "thread_id"],
        },
    },
    {
        "name": "unread_across_accounts",
        "description": (
            "Everything unread in the inbox of every connected mailbox, newest "
            "first. Use for triage: what came in and what still needs a reply."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit_per_account": {"type": "integer", "default": 10}
            },
        },
    },
    {
        "name": "create_draft",
        "description": (
            "Write a draft into a mailbox. It is saved as a draft and is never "
            "sent. Pass thread_id to draft a reply in an existing thread. The "
            "human opens Gmail and sends it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": _ACCOUNT_DESC},
                "to": {"type": "string", "description": "Recipient address."},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain text body."},
                "cc": {"type": "string"},
                "thread_id": {
                    "type": "string",
                    "description": "Optional. Reply inside this thread.",
                },
            },
            "required": ["account", "to", "subject", "body"],
        },
    },
    {
        "name": "upcoming_events_all_accounts",
        "description": (
            "Every event on every connected calendar for the next N days, merged "
            "and sorted by start time. Use for 'what is on my week' across all "
            "accounts at once. Default 7 days ahead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "default": 7},
                "days_back": {
                    "type": "integer",
                    "default": 0,
                    "description": "Also include this many days of past events.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional text filter on title, description, attendees.",
                },
            },
        },
    },
    {
        "name": "calendar_events",
        "description": "Events on one account's primary calendar in a window around now.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": _ACCOUNT_DESC},
                "days_ahead": {"type": "integer", "default": 7},
                "days_back": {"type": "integer", "default": 0},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 25},
            },
            "required": ["account"],
        },
    },
    {
        "name": "search_drive_all_accounts",
        "description": (
            "Search file names and full text across every connected Google Drive "
            "at once, newest first. Plain words work. Drive query syntax also works, "
            "e.g. \"name contains 'pitch' and mimeType contains 'pdf'\"."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10, "description": "Per account."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_drive",
        "description": "Search one account's Drive by name and full text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": _ACCOUNT_DESC},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 15},
            },
            "required": ["account", "query"],
        },
    },
    {
        "name": "recent_drive_files",
        "description": "Most recently modified files in one account's Drive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": _ACCOUNT_DESC},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["account"],
        },
    },
    {
        "name": "read_drive_file",
        "description": (
            "Read a file's contents. Google Docs, Sheets and Slides are exported "
            "as text. Text files are read directly. Binary files return metadata "
            "and a link only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": _ACCOUNT_DESC},
                "file_id": {"type": "string", "description": "id from a search result."},
                "max_chars": {"type": "integer", "default": 30000},
            },
            "required": ["account", "file_id"],
        },
    },
    {
        "name": "recent_chat_all_accounts",
        "description": (
            "Newest Google Chat messages across every space and DM of every "
            "connected account, merged newest first. Use for 'what happened in "
            "Chat today'. Default: last 1 day."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days_back": {"type": "integer", "default": 1},
                "limit_per_space": {"type": "integer", "default": 15},
            },
        },
    },
    {
        "name": "search_chat_all_accounts",
        "description": (
            "Find Google Chat messages containing some text, across every space "
            "and account. Matches message text, sender name and space name. "
            "Chat has no server-side search for users, so this scans recent "
            "messages; widen days_back for older threads."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "days_back": {"type": "integer", "default": 14},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_chat_spaces",
        "description": "Spaces and DMs one account belongs to, most recently active first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": _ACCOUNT_DESC},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["account"],
        },
    },
    {
        "name": "read_chat_space",
        "description": (
            "Recent messages in one space or DM. space is the 'spaces/...' id "
            "from list_chat_spaces or a search result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": _ACCOUNT_DESC},
                "space": {"type": "string"},
                "days_back": {"type": "integer", "default": 7},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["account", "space"],
        },
    },
    {
        "name": "sync_calendars_now",
        "description": (
            "Run the calendar mirror immediately instead of waiting for the "
            "hourly job. Copies every event from the other accounts into the "
            "AIA calendar, updates changed ones, removes ones that vanished. "
            "Returns counts. Safe to run any time."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "calendar_sync_status",
        "description": "Which calendars mirror into which, the window, and the last run on this instance.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call_tool(name: str, args: dict) -> str:
    if name == "list_accounts":
        result = gmail_client.list_accounts()
    elif name == "search_all_inboxes":
        result = gmail_client.search_all(args["query"], args.get("limit", 10))
    elif name == "search_inbox":
        result = gmail_client.search(
            args["account"], args["query"], args.get("limit", 15)
        )
    elif name == "read_thread":
        result = gmail_client.read_thread(args["account"], args["thread_id"])
    elif name == "unread_across_accounts":
        result = gmail_client.unread_summary(args.get("limit_per_account", 10))
    elif name == "create_draft":
        result = gmail_client.create_draft(
            args["account"],
            args["to"],
            args["subject"],
            args["body"],
            args.get("thread_id"),
            args.get("cc"),
        )
    elif name == "upcoming_events_all_accounts":
        result = calendar_client.events_all(
            args.get("days_ahead", 7), args.get("days_back", 0), args.get("query")
        )
    elif name == "calendar_events":
        result = calendar_client.events(
            args["account"],
            args.get("days_ahead", 7),
            args.get("days_back", 0),
            args.get("query"),
            args.get("limit", 25),
        )
    elif name == "search_drive_all_accounts":
        result = drive_client.search_all(args["query"], args.get("limit", 10))
    elif name == "search_drive":
        result = drive_client.search(args["account"], args["query"], args.get("limit", 15))
    elif name == "recent_drive_files":
        result = drive_client.recent(args["account"], args.get("limit", 20))
    elif name == "read_drive_file":
        result = drive_client.read_file(
            args["account"], args["file_id"], args.get("max_chars", 30000)
        )
    elif name == "recent_chat_all_accounts":
        result = chat_client.recent_all(args.get("days_back", 1), args.get("limit_per_space", 15))
    elif name == "search_chat_all_accounts":
        result = chat_client.search_all(args["query"], args.get("days_back", 14))
    elif name == "list_chat_spaces":
        result = chat_client.spaces(args["account"], args.get("limit", 50))
    elif name == "read_chat_space":
        result = chat_client.messages(
            args["account"], args["space"], args.get("days_back", 7), args.get("limit", 50)
        )
    elif name == "sync_calendars_now":
        result = calendar_sync.run()
    elif name == "calendar_sync_status":
        result = calendar_sync.status()
    else:
        raise ValueError(f"Unknown tool: {name}")
    return json.dumps(result, indent=2, default=str)


def handle_rpc(message: dict) -> dict | None:
    """Handle one JSON-RPC message. Returns None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, text):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    if method == "initialize":
        return ok(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            }
        )
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            text = call_tool(name, args)
            return ok({"content": [{"type": "text", "text": text}]})
        except Exception as exc:  # surfaced to the model, not swallowed
            return ok(
                {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                }
            )
    return err(-32601, f"Method not found: {method}")
