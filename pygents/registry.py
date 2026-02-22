from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Generic, TypeVar

from pygents.errors import (
    UnregisteredAgentError,
    UnregisteredHookError,
    UnregisteredToolError,
)

if TYPE_CHECKING:
    from pygents.hooks import Hook, HookType
    from pygents.tool import AsyncGenTool, Tool

T = TypeVar("T")


class BaseRegistry(ABC, Generic[T]):
    _registry: ClassVar[dict] = {}
    _key_attr: ClassVar[str]
    _not_found_error: ClassVar[type[Exception]]
    _allow_reregister: ClassVar[bool] = False

    @classmethod
    def clear(cls) -> None:
        cls._registry = {}

    @classmethod
    def register(cls, item: T) -> None:
        key = getattr(item, cls._key_attr)
        existing = cls._registry.get(key)
        if existing is not None:
            if cls._allow_reregister and existing is item:
                return
            raise ValueError(f"{key!r} already registered")
        cls._registry[key] = item

    @classmethod
    def get(cls, name: str) -> T:
        item = cls._registry.get(name)
        if item is None:
            raise cls._not_found_error(f"{name!r} not found")
        return item  # type: ignore[return-value]


class ToolRegistry(BaseRegistry):  # type: ignore[type-arg]
    """Registry for Tools. Not meant to be instantiated or used directly."""

    _registry: ClassVar[dict] = {}
    _key_attr = "__name__"
    _not_found_error = UnregisteredToolError

    @classmethod
    def all(cls) -> list[Tool | AsyncGenTool]:
        """Return all registered Tools."""
        return list(cls._registry.values())


class AgentRegistry(BaseRegistry):  # type: ignore[type-arg]
    """Registry for Agents. Not meant to be instantiated or used directly."""

    _registry: ClassVar[dict] = {}
    _key_attr = "name"
    _not_found_error = UnregisteredAgentError


class HookRegistry(BaseRegistry):  # type: ignore[type-arg]
    """Registry for Hooks. Not meant to be instantiated or used directly."""

    _registry: ClassVar[dict] = {}
    _key_attr = "__name__"
    _not_found_error = UnregisteredHookError
    _allow_reregister = True

    @classmethod
    def wrap(cls, fn: Callable[..., Any], hook_type: HookType) -> Hook:
        """Wrap *fn* as a registered Hook, or return the existing wrapper.

        - If *fn* is already a Hook (has ``.metadata``), re-register and return it.
        - If a Hook wrapping the same underlying function was previously registered
          under the same ``__name__``, return that existing wrapper.
        - Otherwise, delegate to ``hook(hook_type)`` to create, register, and return
          a new Hook.
        """
        if hasattr(fn, "metadata"):
            cls.register(fn)
            return fn  # type: ignore[return-value]

        name = getattr(fn, "__name__", None)
        if name:
            try:
                existing = cls.get(name)
                if getattr(existing, "fn", None) is fn:
                    return existing  # type: ignore[return-value]
            except UnregisteredHookError:
                pass

        from pygents.hooks import hook as _hook_decorator

        return _hook_decorator(hook_type)(fn)

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
