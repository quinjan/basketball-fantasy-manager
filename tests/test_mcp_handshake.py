"""The stdio MCP server must accept the legacy initialize even after a server/discover probe (Copilot CLI race)."""

import json
import subprocess
import sys
import time

DISCOVER = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "server/discover",
    "params": {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    },
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "initialize",
    "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
}
TOOLS = {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}


def test_initialize_after_timed_out_discover_probe():
    lines = [DISCOVER, INITIALIZE, {"jsonrpc": "2.0", "method": "notifications/initialized"}, TOOLS]
    proc = subprocess.Popen(
        [sys.executable, "-m", "fbm.mcp_server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        proc.stdin.write("".join(json.dumps(m) + "\n" for m in lines))
        proc.stdin.flush()  # keep stdin open: EOF would shut the server down before it answers tools/list
        replies: dict = {}
        deadline = time.time() + 120
        while 3 not in replies and time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            if "id" in msg:
                replies[msg["id"]] = msg
    finally:
        proc.kill()
        proc.wait()
    assert "error" in replies[1]
    assert replies[2]["result"]["protocolVersion"] == "2025-11-25"
    assert len(replies[3]["result"]["tools"]) > 30
