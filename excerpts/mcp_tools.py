# Excerpt from the private Pink Anchor repository.
# Trimmed for readability; helper functions and imports are omitted.
#
# MCP server exposing the personal data hub to LLM clients.
# Design choice: 7 merged tools (pa_memory, pa_window, pa_diary, pa_cycle,
# pa_gallery, pa_health, pa_memo), each taking an `action` argument, instead of
# 36 single-purpose tools. Every tool schema is sent to the model in each
# conversation, so fewer tools = fewer tokens spent before the chat starts
# (the whole set is roughly 2.5k tokens).
#
# All tools except pa_health call the main API over HTTP; the API owns the
# write pipeline and validation. pa_health reads a separate SQLite file read-only.

import json
import os
import httpx
from fastmcp import FastMCP

API_BASE = os.environ["API_BASE_URL"]   # internal service address
HEADERS = auth_headers()                # credentials come from the environment, never from code
TIMEOUT = 15

mcp = FastMCP("pa-mcp")


async def _api(method: str, path: str, body: dict | None = None,
               params: dict | None = None) -> dict:
    """Single HTTP helper. Errors come back as data, so the model sees a readable
    message instead of a crashed tool call."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.request(method, f"{API_BASE}{path}", headers=HEADERS,
                                json=body, params=params)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        return {"error": e.response.text[:300], "status_code": e.response.status_code}
    except Exception as e:
        return {"error": str(e)}


def _out(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


@mcp.tool()
async def pa_memory(
    action: str,
    query: str = "",
    id: int = 0,
    what: str = "",
    category: str = "",
    tag: str = "",
    importance: int = 0,
    keywords: str = "",
    time_ref: str = "",
    limit: int = 0,
) -> str:
    """Memory CRUD. action: search(query,limit,category) | create(what,...) |
    update(id,...) | delete(id) | audit(category,tag, query=page number)"""
    optional = {"category": category, "tag": tag, "keywords": keywords,
                "importance": importance, "time_ref": time_ref}
    fields = {k: v for k, v in optional.items() if v}

    if action == "search":
        if not query:
            return _out({"error": "search requires query"})
        body = {"query": query, "limit": limit or 3}
        if category:
            body["category"] = category
        return _out(await _api("POST", "/api/recall", body=body))

    if action == "create":
        if not what:
            return _out({"error": "create requires what"})
        # The API runs the full write pipeline (rate limit, dedup, merge_key merge).
        return _out(await _api("POST", "/api/recall/write", body={"what": what, **fields}))

    if action == "update":
        if not id:
            return _out({"error": "update requires id"})
        body = ({"what": what} if what else {}) | fields
        if not body:
            return _out({"error": "update requires at least one field"})
        return _out(await _api("PUT", f"/api/recall/{id}", body=body))

    if action == "delete":
        if not id:
            return _out({"error": "delete requires id"})
        return _out(await _api("DELETE", f"/api/recall/{id}"))   # soft delete server-side

    if action == "audit":
        page = int(query) if query.isdigit() else 1
        params = {"page": page, "page_size": 20, **{k: v for k, v in
                  {"category": category, "tag": tag}.items() if v}}
        data = await _api("GET", "/api/recall/audit", params=params)
        if "data" in data:   # trim each row to the fields the model needs
            keep = ("id", "category", "tag", "what", "time_ref", "importance")
            data["data"] = [{k: row[k] for k in keep if k in row} for row in data["data"]]
        return _out(data)

    return _out({"error": f"unknown action: {action}. options: search/create/update/delete/audit"})


@mcp.tool()
async def pa_gallery(action: str, filename: str = "", title: str = "", content: str = "") -> str:
    """Gallery pages. action: list | get(filename) | create(filename,title,content)"""
    if action == "list":
        return _out(await _api("GET", "/api/gallery"))
    if action == "get":
        if not filename:
            return _out({"error": "get requires filename"})
        return _out(await _api("GET", f"/api/gallery/{filename}"))
    if action == "create":
        if not filename or not content:
            return _out({"error": "create requires filename and content"})
        body = {"filename": filename, "content": content} | ({"title": title} if title else {})
        return _out(await _api("POST", "/api/gallery", body=body))
    return _out({"error": f"unknown action: {action}. options: list/get/create"})
