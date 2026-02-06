# TODO: Postpone evaluation of annotations so `dict[str, Tool]` etc. are not evaluated at class body time, when `Tool` is only imported under TYPE_CHECKING.
from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING

from app.errors import UnregisteredAgentError, UnregisteredToolError

if TYPE_CHECKING:
    from app.agent import Agent
    from app.tool import Tool


class ToolRegistry(ABC):
    """
    Registry class for Tools. It is not meant to be instantiated or used directly.
    """

    _registry: dict[str, Tool] = {}

    @classmethod
    def register(cls, tool: Tool) -> None:
        """
        Register a Tool.

        Args:
            tool: The Tool to register.

        Raises:
            ValueError if the Tool is already registered.
        """
        if tool.__name__ in cls._registry:
            raise ValueError(f"Tool {tool.__name__!r} already registered")
        cls._registry[tool.__name__] = tool

    @classmethod
    def get(cls, name: str) -> Tool:
        """
        Get a Tool by name.

        Args:
            name: The name of the Tool.

        Returns:
            The Tool.

        Raises:
            UnregisteredToolError if the Tool is not found.
        """
        tool = cls._registry.get(name)
        if tool is None:
            raise UnregisteredToolError(f"Tool {name!r} not found")
        return tool


class AgentRegistry(ABC):
    """
    Registry class for Agents. It is not meant to be instantiated or used directly.
    """

    _registry: dict[str, Agent] = {}

    @classmethod
    def clear(cls) -> None:
        cls._registry = {}

    @classmethod
    def register(cls, agent: Agent) -> None:
        """
        Register an Agent.
        """
        if agent.name in cls._registry:
            raise ValueError(f"Agent {agent.name!r} already registered")
        cls._registry[agent.name] = agent

    @classmethod
    def get(cls, name: str) -> Agent:
        """
        Get an Agent by name.

        Raises:
            UnregisteredAgentError if the Agent is not found.
        """
        agent = cls._registry.get(name)
        if agent is None:
            raise UnregisteredAgentError(f"Agent {name!r} not found")
        return agent
