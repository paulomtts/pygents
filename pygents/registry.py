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
    Registry for Tools. Not meant to be instantiated or used directly.
    """

    _registry: dict[str, Tool] = {}

    # -- registration ---------------------------------------------------------

    @classmethod
    def register(cls, tool: Tool) -> None:
        """Register a Tool.

        Parameters
        ----------
        tool : Tool
            The Tool to register.

        Raises
        ------
        ValueError
            If the Tool is already registered.
        """
        if tool.__name__ in cls._registry:
            raise ValueError(f"Tool {tool.__name__!r} already registered")
        cls._registry[tool.__name__] = tool

    # -- lookup ----------------------------------------------------------------

    @classmethod
    def get(cls, name: str) -> Tool:
        """Get a Tool by name.

        Parameters
        ----------
        name : str
            The name of the Tool.

        Returns
        -------
        Tool
            The registered Tool.

        Raises
        ------
        UnregisteredToolError
            If the Tool is not found.
        """
        tool = cls._registry.get(name)
        if tool is None:
            raise UnregisteredToolError(f"Tool {name!r} not found")
        return tool

    @classmethod
    def all(cls) -> list[Tool]:
        """Return all registered Tools."""
        return list(cls._registry.values())


class AgentRegistry(ABC):
    """
    Registry for Agents. Not meant to be instantiated or used directly.
    """

    _registry: dict[str, Agent] = {}

    # -- registration ---------------------------------------------------------

    @classmethod
    def clear(cls) -> None:
        cls._registry = {}

    @classmethod
    def register(cls, agent: Agent) -> None:
        """Register an Agent.

        Parameters
        ----------
        agent : Agent
            The Agent to register.

        Raises
        ------
        ValueError
            If the Agent is already registered.
        """
        if agent.name in cls._registry:
            raise ValueError(f"Agent {agent.name!r} already registered")
        cls._registry[agent.name] = agent

    # -- lookup ----------------------------------------------------------------

    @classmethod
    def get(cls, name: str) -> Agent:
        """Get an Agent by name.

        Parameters
        ----------
        name : str
            The name of the Agent.

        Returns
        -------
        Agent
            The registered Agent.

        Raises
        ------
        UnregisteredAgentError
            If the Agent is not found.
        """
        agent = cls._registry.get(name)
        if agent is None:
            raise UnregisteredAgentError(f"Agent {name!r} not found")
        return agent


class HookRegistry(ABC):
    """
    Registry for Hooks. Not meant to be instantiated or used directly.
    """

    _registry: dict[str, Hook] = {}

    # -- registration ---------------------------------------------------------

    @classmethod
    def clear(cls) -> None:
        cls._registry = {}

    @classmethod
    def register(cls, hook: Hook, name: str | None = None) -> None:
        """Register a Hook.

        Parameters
        ----------
        hook : Hook
            The Hook to register.
        name : str | None
            Name to register under; uses hook.__name__ if not provided.

        Raises
        ------
        ValueError
            If the Hook is already registered.
        """
        hook_name = name or hook.__name__
        if hook_name in cls._registry:
            raise ValueError(f"Hook {hook_name!r} already registered")
        cls._registry[hook_name] = hook

    # -- lookup ----------------------------------------------------------------

    @classmethod
    def get(cls, name: str) -> Hook:
        """Get a Hook by name.

        Parameters
        ----------
        name : str
            The name of the Hook.

        Returns
        -------
        Hook
            The registered Hook.

        Raises
        ------
        UnregisteredHookError
            If the Hook is not found.
        """
        hook = cls._registry.get(name)
        if hook is None:
            raise UnregisteredHookError(f"Hook {name!r} not found")
        return hook
