from typing import Any

from friday.orchestration.tool_contracts import Tool, ToolRegistrationError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any, Any]] = {}

    def register(self, tool: Tool[Any, Any]) -> None:
        tool.contract.validate()
        if tool.contract.name in self._tools:
            raise ToolRegistrationError(f"{tool.contract.name!r}: already registered")
        self._tools[tool.contract.name] = tool

    def get(self, name: str) -> Tool[Any, Any] | None:
        return self._tools.get(name)

    def all(self) -> list[Tool[Any, Any]]:
        return list(self._tools.values())
