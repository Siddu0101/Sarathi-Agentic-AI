"""
mcp_client.py — Lightweight MCP stdio client for @mongodb-js/mongodb-mcp-server
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The MongoDB MCP server uses stdio transport (JSON-RPC 2.0, newline-delimited).
This client:
  1. Launches @mongodb-js/mongodb-mcp-server as a subprocess
  2. Performs the MCP initialization handshake
  3. Exposes call_tool(name, args) → dict  for the agent to use
  4. Lists available tools so the agent can declare them to Gemini

Usage (from sarathi_agent.py):
    mcp = MongoMCPClient(os.environ.get("MONGODB_URI", ""))
    mcp.start()                           # launch subprocess + handshake
    tools = mcp.list_tools()             # get tool schemas for Gemini
    result = mcp.call_tool("find", {     # call a MongoDB tool
        "collection": "users",
        "filter": {"mobile": "9999999999"},
        "limit": 1,
    })
    mcp.stop()
"""

import json
import os
import subprocess
import threading
import time
from typing import Any


class MongoMCPClient:
    """
    Subprocess-based MCP client for the MongoDB MCP server.
    Thread-safe: uses a lock around every request/response pair.
    """

    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._req_id = 0
        self._available_tools: list[dict] = []
        self._ready = False

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self, timeout: float = 20.0) -> bool:
        """
        Launch the MCP server subprocess and complete the initialization handshake.
        Returns True on success, False if npx is unavailable or handshake fails.
        """
        if not self.connection_string:
            print("[MCP] MONGODB_URI not set — skipping MCP server launch")
            return False

        try:
            self.proc = subprocess.Popen(
                [
                    "npx", "-y",
                    "@mongodb-js/mongodb-mcp-server",
                    "--connectionString", self.connection_string,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            print("[MCP] ❌  npx not found. Install Node.js from https://nodejs.org")
            return False

        # Give the server a moment to start
        time.sleep(1.0)

        if self.proc.poll() is not None:
            err = self.proc.stderr.read()
            print(f"[MCP] ❌  MCP server exited immediately: {err[:200]}")
            return False

        # ── Handshake Step 1: initialize ──────────────────────────────────────
        resp = self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "sarathi-agent", "version": "1.0"},
        }, timeout=timeout)

        if not resp or "error" in resp:
            print(f"[MCP] ❌  Initialization failed: {resp}")
            self.stop()
            return False

        # ── Handshake Step 2: notifications/initialized ───────────────────────
        self._notify("notifications/initialized", {})

        # ── Discover tools ────────────────────────────────────────────────────
        tools_resp = self._rpc("tools/list", {}, timeout=10.0)
        if tools_resp and "result" in tools_resp:
            self._available_tools = tools_resp["result"].get("tools", [])
            print(f"[MCP] ✅  Connected — {len(self._available_tools)} tools available: "
                  f"{[t['name'] for t in self._available_tools]}")
        else:
            print("[MCP] ⚠️  Could not list tools, continuing anyway")

        self._ready = True
        return True

    def stop(self):
        """Terminate the MCP server subprocess."""
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                pass
            self.proc = None
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready and self.proc is not None and self.proc.poll() is None

    # ── public API ────────────────────────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        """Return the list of tool schemas from the MCP server."""
        return self._available_tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        """
        Invoke a MongoDB MCP tool by name.
        Returns the result dict, or {"error": "..."} on failure.
        """
        if not self.is_ready:
            return {"error": "MCP server not running"}

        resp = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if not resp:
            return {"error": "No response from MCP server"}
        if "error" in resp:
            return {"error": str(resp["error"])}

        result = resp.get("result", {})
        # MCP tool results come as {"content": [{"type": "text", "text": "..."}]}
        content = result.get("content", [])
        if content and content[0].get("type") == "text":
            raw = content[0]["text"]
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"result": raw}
        return result

    def gemini_tool_declarations(self) -> list[dict]:
        """
        Convert MCP tool schemas into Gemini FunctionDeclaration-compatible dicts.
        These are passed to the Vertex AI / Gemini agent loop.
        """
        declarations = []
        for tool in self._available_tools:
            decl = {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema", {
                    "type": "object",
                    "properties": {},
                }),
            }
            declarations.append(decl)
        return declarations

    # ── internal JSON-RPC ────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _rpc(self, method: str, params: dict, timeout: float = 10.0) -> dict | None:
        """Send a JSON-RPC request and wait for the matching response."""
        if not self.proc:
            return None

        req_id = self._next_id()
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "id": req_id,
            "params": params,
        })

        with self._lock:
            try:
                self.proc.stdin.write(msg + "\n")
                self.proc.stdin.flush()
            except BrokenPipeError:
                print("[MCP] ❌  MCP server pipe broken")
                self._ready = False
                return None

            # Read lines until we find our response id
            deadline = time.time() + timeout
            while time.time() < deadline:
                self.proc.stdout._CHUNK_SIZE = 1  # type: ignore[attr-defined]
                line = self.proc.stdout.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                try:
                    data = json.loads(line.strip())
                    if data.get("id") == req_id:
                        return data
                except json.JSONDecodeError:
                    continue

            print(f"[MCP] ⚠️  Timeout waiting for response to {method!r}")
            return None

    def _notify(self, method: str, params: dict):
        """Send a JSON-RPC notification (no id, no response expected)."""
        if not self.proc:
            return
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        try:
            self.proc.stdin.write(msg + "\n")
            self.proc.stdin.flush()
        except BrokenPipeError:
            pass


# ── Module-level singleton (shared across the Flask app) ──────────────────────
# Initialized in sarathi_agent.py's init_mcp() and reused by the agent loop.
_mcp_singleton: MongoMCPClient | None = None


def get_mcp_client() -> MongoMCPClient | None:
    """Return the running MCP client, or None if not started."""
    return _mcp_singleton if (_mcp_singleton and _mcp_singleton.is_ready) else None


def init_mcp(connection_string: str | None = None) -> MongoMCPClient | None:
    """
    Start (or return the already-running) MongoDB MCP client.
    Called once at application startup.
    """
    global _mcp_singleton
    if _mcp_singleton and _mcp_singleton.is_ready:
        return _mcp_singleton

    uri = connection_string or os.environ.get("MONGODB_URI", "")
    client = MongoMCPClient(uri)
    if client.start():
        _mcp_singleton = client
        return _mcp_singleton
    return None
