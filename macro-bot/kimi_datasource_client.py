"""
kimi_datasource_client.py - Kimi professional database MCP client wrapper.

Works locally and on servers. Starts the local kimi-datasource plugin via stdio
and calls Kimi member data sources (Tonghuashun, Yahoo Finance, World Bank,
Tianyancha, arXiv, scholar, Yuandian law, Wind, IMF, SEC EDGAR, S&P, etc.)
through the MCP protocol.

Dependencies:
    pip install mcp  # Python >= 3.10

Environment variables:
    KIMI_CODE_OAUTH_HOST  default https://auth.kimi.com
    KIMI_CODE_BASE_URL    default https://api.kimi.com/coding/v1
    KIMI_DATASOURCE_NODE  Node executable path, default /opt/homebrew/bin/node (macOS)
    KIMI_DATASOURCE_SCRIPT path to kimi-datasource.mjs

Public API:
    kimi_call(tool_name, args) -> Optional[Dict[str, Any]]
    get_data_source_desc(name) -> Optional[Dict]
    call_data_source(data_source_name, api_name, params) -> Optional[Dict]
    query_data_source(data_source_name, api_name, params) -> Optional[str]

Failure handling:
    - Expired/missing credentials return a clear error suggesting `kimi login`
    - Missing plugin/Node returns actionable error text
    - Transient network timeouts return None, caller can retry
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError as e:
    raise ImportError(
        "kimi_datasource_client requires the `mcp` package. Run: "
        "`/Users/2/.hermes/hermes-agent/venv/bin/python -m pip install mcp` or "
        "`uv pip install mcp` (Python >= 3.10)"
    ) from e

DEFAULT_NODE = "/opt/homebrew/bin/node"
DEFAULT_SCRIPT = os.path.expanduser(
    "~/.kimi-code/plugins/managed/kimi-datasource/bin/kimi-datasource.mjs"
)
DEFAULT_OAUTH_HOST = "https://auth.kimi.com"
DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"

VALID_DATA_SOURCES = [
    "stock_finance_data",
    "yahoo_finance",
    "world_bank_open_data",
    "tianyancha",
    "arxiv",
    "scholar",
    "yuandian_law",
    "wind",
    "imf",
    "gildata",
    "sec_edgar",
    "sp_data",
]


def _node_path() -> str:
    return os.getenv("KIMI_DATASOURCE_NODE", DEFAULT_NODE)


def _script_path() -> str:
    return os.getenv("KIMI_DATASOURCE_SCRIPT", DEFAULT_SCRIPT)


def _oauth_host() -> str:
    return os.getenv("KIMI_CODE_OAUTH_HOST", DEFAULT_OAUTH_HOST)


def _base_url() -> str:
    return os.getenv("KIMI_CODE_BASE_URL", DEFAULT_BASE_URL)


def _check_prerequisites() -> Optional[str]:
    node = _node_path()
    script = _script_path()
    if not os.path.isfile(node):
        return f"Node not found: {node}. Set KIMI_DATASOURCE_NODE or install Node.js."
    if not os.path.isfile(script):
        return (
            f"kimi-datasource plugin not found: {script}. "
            "Install Kimi CLI and the kimi-datasource plugin first."
        )
    return None


@asynccontextmanager
async def _mcp_session():
    err = _check_prerequisites()
    if err:
        raise RuntimeError(err)

    server_params = StdioServerParameters(
        command=_node_path(),
        args=[_script_path()],
        env={
            **os.environ,
            "KIMI_CODE_OAUTH_HOST": _oauth_host(),
            "KIMI_CODE_BASE_URL": _base_url(),
        },
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _extract_text(result: Optional[Dict[str, Any]]) -> str:
    if not result or not isinstance(result, dict):
        return ""
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)


def _is_auth_error(text: str) -> bool:
    lower = text.lower()
    keywords = ["unauthorized", "invalid token", "access_token", "login", "凭证", "未授权"]
    return any(k in lower for k in keywords)


async def kimi_call(tool_name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Generic MCP tool call. Returns result dict or None on failure."""
    try:
        async with _mcp_session() as session:
            result = await session.call_tool(tool_name, args)
            return result.model_dump()
    except Exception as e:
        msg = str(e)
        if _is_auth_error(msg):
            print(
                "[ERROR] Kimi credentials expired or not logged in. Run `kimi login` to refresh.",
                file=sys.stderr,
            )
        else:
            print(f"[ERROR] MCP {tool_name} failed: {msg}", file=sys.stderr)
        return None


async def get_data_source_desc(name: str) -> Optional[Dict[str, Any]]:
    """Fetch API documentation for a named data source."""
    if name not in VALID_DATA_SOURCES:
        print(
            f"[WARN] Unknown data source: {name}. Valid: {VALID_DATA_SOURCES}",
            file=sys.stderr,
        )
    result = await kimi_call("get_data_source_desc", {"name": name})
    if not result:
        return None
    text = _extract_text(result)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"description_text": text}


async def call_data_source(
    data_source_name: str,
    api_name: str,
    params: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Call a specific API on a specific data source."""
    return await kimi_call(
        "call_data_source_tool",
        {
            "data_source_name": data_source_name,
            "api_name": api_name,
            "params": params,
        },
    )


async def query_data_source(
    data_source_name: str,
    api_name: str,
    params: Dict[str, Any],
) -> Optional[str]:
    """Call a data source API and return the text result directly."""
    result = await call_data_source(data_source_name, api_name, params)
    if not result:
        return None
    return _extract_text(result)


# Synchronous wrappers for use in regular scripts / Flask handlers


def run_kimi_call(tool_name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return asyncio.run(kimi_call(tool_name, args))


def run_get_desc(name: str) -> Optional[Dict[str, Any]]:
    return asyncio.run(get_data_source_desc(name))


def run_query(data_source_name: str, api_name: str, params: Dict[str, Any]) -> Optional[str]:
    return asyncio.run(query_data_source(data_source_name, api_name, params))


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        ds = sys.argv[1]
        api = sys.argv[2]
        params = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        text = run_query(ds, api, params)
        print(text if text else "<no result>")
    else:
        print("Usage: python kimi_datasource_client.py <data_source> <api_name> '[params_json]'")
        print(f"Valid data sources: {VALID_DATA_SOURCES}")
