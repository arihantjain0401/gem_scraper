"""Tool registry — same shape as Bento's backend/mcp/registry.py (sync handlers).

Bento's handlers are async; this server is Flask + waitress, so handlers
are plain sync functions. The wire output of list_tools is identical:
{name, description, input_schema, tags}.
"""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema for parameters
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    tags: list[str] = field(default_factory=list)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        if tool.name in self._tools:
            raise ValueError("Tool '%s' is already registered" % tool.name)
        self._tools[tool.name] = tool

    def get(self, name):
        return self._tools.get(name)

    def list_tools(self, tag=None):
        tools = list(self._tools.values())
        if tag:
            tools = [t for t in tools if tag in t.tags]
        return tools

    @property
    def count(self):
        return len(self._tools)
