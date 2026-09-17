"""Full-board GeM monitor: snapshot all active bids, diff snapshots, MCP surface.

Layers:
  core (no transport)  — db.py, vitals.py, crawl.py, detail.py, probe.py
  MCP HTTP (primary)   — mcp_contract.py, registry.py, mcp_tools.py, mcp_server.py
  thin CLI             — cli.py (humans + launchd)

The MCP server speaks Bento's locked HTTP+JSON convention (ok()/fail() inner
envelope, X-API-Key) so Bento can reach it later with a routing-table entry.
"""
