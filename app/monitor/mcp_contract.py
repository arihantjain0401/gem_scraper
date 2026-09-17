"""MCP result contract — shape-identical to Bento's backend/mcp/contract.py.

Every monitor tool returns ok()/fail(); the HTTP layer wraps that inner
envelope in {"success": bool, "result": ..., "error": ...}. Keeping the
inner envelope identical means Bento's HttpMCPDispatcher can consume this
server with a routing-table entry and zero changes on either side.
"""


def ok(data=None):
    """Successful tool result."""
    result = {"ok": True}
    if data is not None:
        result["data"] = data
    return result


def fail(error):
    """Failed tool result."""
    return {"ok": False, "error": str(error)}
