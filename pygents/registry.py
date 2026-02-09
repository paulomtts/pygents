# TODO: Postpone evaluation of annotations so `dict[str, Tool]` etc. are not evaluated at class body time, when `Tool` is only imported under TYPE_CHECKING.
from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING

from pygents.errors import (
    UnregisteredAgentError,
    UnregisteredHookError,
    UnregisteredToolError,
)

if TYPE_CHECKING:
    from pygents.agent import Agent
    from pygents.hooks import Hook
    from pygents.tool import Tool


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

    @classmethod
    def all(cls) -> list[Tool]:
        """
        Get all registered Tools.
        """
        return list(cls._registry.values())


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


class HookRegistry(ABC):
    """
    Registry class for Hooks. It is not meant to be instantiated or used directly.
    """

    _registry: dict[str, Hook] = {}

    @classmethod
    def clear(cls) -> None:
        cls._registry = {}

    @classmethod
    def register(cls, hook: Hook, name: str | None = None) -> None:
        """
        Register a Hook.

        Args:
            hook: The Hook to register.
            name: The name to register the hook under. Uses hook.__name__ if not provided.

        Raises:
            ValueError if the Hook is already registered.
        """
        hook_name = name or hook.__name__
        if hook_name in cls._registry:
            raise ValueError(f"Hook {hook_name!r} already registered")
        cls._registry[hook_name] = hook

    @classmethod
    def get(cls, name: str) -> Hook:
        """
        Get a Hook by name.

        Args:
            name: The name of the Hook.

        Returns:
            The Hook.

        Raises:
            UnregisteredHookError if the Hook is not found.
        """
        hook = cls._registry.get(name)
        if hook is None:
            raise UnregisteredHookError(f"Hook {name!r} not found")
        return hook
