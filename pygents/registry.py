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
    def register(
        cls,
        hook: Hook,
        name: str | None = None,
        hook_type: object | None = None,
    ) -> None:
        """Register a Hook.

        Parameters
        ----------
        hook : Hook
            The hook to register.
        name : str | None
            Name to register under; uses hook.__name__ if not provided.
        hook_type : enum member | None
            If provided, stored on the hook for get_by_type lookups.

        Raises
        ------
        ValueError
            If the hook is already registered.
        """
        hook_name = name or getattr(hook, "__name__", None) or "hook"
        existing = cls._registry.get(hook_name)
        if existing is not None and existing is not hook:
            raise ValueError(f"Hook {hook_name!r} already registered")
        cls._registry[hook_name] = hook
        if hook_type is not None:
            hook.type = hook_type  # type: ignore[attr-defined]

    # -- lookup ----------------------------------------------------------------

    @classmethod
    def get(cls, name: str) -> Hook:
        """Get a hook by name.

        Parameters
        ----------
        name : str
            The name of the hook.

        Returns
        -------
        Hook
            The registered hook.

        Raises
        ------
        UnregisteredHookError
            If the hook is not found.
        """
        h = cls._registry.get(name)
        if h is None:
            raise UnregisteredHookError(f"Hook {name!r} not found")
        return h

    @classmethod
    def get_by_type(cls, hook_type: object, hooks: list[Hook]) -> list[Hook]:
        """Return all hooks in the given list that match the hook_type, in order."""

        def matches(h: "Hook") -> bool:
            ht = getattr(h, "type", None)
            if ht is None:
                return False
            if isinstance(ht, (tuple, frozenset)):
                return hook_type in ht
            return ht == hook_type

        return [h for h in hooks if matches(h)]
