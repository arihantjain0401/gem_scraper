"""MCP HTTP server (Flask + waitress) — Bento's exact wire contract.

create_mcp_app(registry, title) exposes the registry over HTTP:
  GET  /tools[?tag=]  -> [{name, description, input_schema, tags}]
  POST /call          -> {"tool", "params"} -> {"success", "result", "error"}
  GET  /health        -> {"status", "tools_count"}

The tool result is the ok()/fail() inner envelope from mcp_contract.py.
Auth: X-API-Key must match credentials/mcp_api_key.env when set (unset =
open), identical to Bento's backend/mcp/server.py. Unknown tool -> HTTP 404.
Handlers never raise — every failure is a fail(...) result, never a 500.

Framework choice: Flask + waitress because both are already in
requirements.txt — the contract is HTTP-level, the framework is irrelevant
to Bento's HttpMCPDispatcher.
"""

from flask import Flask, jsonify, request

from app.config import MCP_HOST, MCP_PORT, load_config, load_mcp_api_key
from app.monitor.mcp_tools import register_monitor_tools
from app.monitor.registry import ToolRegistry


def _verify_api_key(configured_key):
    """Reject requests without the expected X-API-Key (only when one is set)."""
    if not configured_key:
        return
    if request.headers.get("X-API-Key", "") != configured_key:
        from flask import abort
        abort(401, description="Invalid or missing API key")


def create_mcp_app(registry, config, title="Gem Monitor MCP Server"):
    """Build the Flask app serving the given registry (Bento wire format)."""
    api_key = load_mcp_api_key()
    app = Flask(title)

    @app.get("/tools")
    def list_tools():
        _verify_api_key(api_key)
        tag = request.args.get("tag")
        return jsonify([
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "tags": tool.tags,
            }
            for tool in registry.list_tools(tag=tag)
        ])

    @app.post("/call")
    def call_tool():
        _verify_api_key(api_key)
        payload = request.get_json(silent=True) or {}
        tool = registry.get(payload.get("tool", ""))
        if tool is None:
            from flask import abort
            abort(404, description="Tool '%s' not found" % payload.get("tool"))
        try:
            result = tool.handler(payload.get("params") or {})
            return jsonify({"success": True, "result": result})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)})

    @app.get("/health")
    def health():
        _verify_api_key(api_key)
        return jsonify({"status": "ok", "tools_count": registry.count})

    return app


def build_app(config=None):
    """Registry + tools + Flask app, ready for waitress."""
    config = config or load_config()
    registry = ToolRegistry()
    register_monitor_tools(registry, config)
    return create_mcp_app(registry, config)


def main():
    """waitress entry point (the launchd com.gemscraper.monitor.api job)."""
    from app.monitor import db as monitor_db
    from app.config import MCP_HOST, MCP_PORT
    from waitress import serve

    config = load_config()
    monitor_db.init()
    app = build_app(config)
    print("Gem Monitor MCP server on %s:%d (tools: 7)" % (MCP_HOST, MCP_PORT),
          flush=True)
    serve(app, host=MCP_HOST, port=MCP_PORT, threads=8)


if __name__ == "__main__":
    main()
