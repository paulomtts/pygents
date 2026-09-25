from __future__ import annotations

import asyncio
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
    _allow_reregister: ClassVar[bool] = False
    _not_found_error: ClassVar[type[Exception]] = UnregisteredHookError

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
        return item

    @classmethod
    def unregister(cls, name: str) -> None:
        """Remove *name* so it can no longer be looked up and can be reused.

        Only lookup by name is affected: objects that already hold the item
        (e.g. an agent holding a tool) keep working.

        Raises ``cls._not_found_error`` if *name* is not registered.
        """
        if name not in cls._registry:
            raise cls._not_found_error(f"{name!r} not found")
        del cls._registry[name]


class ToolRegistry(BaseRegistry):
    """Registry for Tools. Not meant to be instantiated or used directly."""

    _registry: ClassVar[dict] = {}
    _key_attr = "__name__"
    _not_found_error = UnregisteredToolError

    @classmethod
    def all(cls) -> list[Tool | AsyncGenTool]:
        """Return all registered Tools."""
        return list(cls._registry.values())

    @classmethod
    def definitions(cls) -> list[dict[str, Any]]:
        """Return doc_tree() for every root-level tool (tools not registered as subtools)."""
        root_tools = [t for t in cls.all() if "." not in t.__name__]
        return [t.doc_tree() for t in sorted(root_tools, key=lambda t: t.__name__)]


class AgentRegistry(BaseRegistry):
    """Registry for Agents. Not meant to be instantiated or used directly."""

    _registry: ClassVar[dict] = {}
    _key_attr = "name"
    _not_found_error = UnregisteredAgentError


class HookRegistry(BaseRegistry):
    """Registry for Hooks. Not meant to be instantiated or used directly."""

    _registry: ClassVar[dict] = {}
    _global_hooks: ClassVar[list] = []
    _key_attr = "__name__"
    _allow_reregister = True

    @classmethod
    def clear(cls) -> None:
        super().clear()
        cls._global_hooks = []

    @classmethod
    def unregister(cls, name: str) -> None:
        """Remove *name* and stop that same hook object from firing globally.

        Resolves the hook first, so an unknown name raises
        ``UnregisteredHookError`` without changing ``_global_hooks``.
        Instance hook lists (``obj.hooks``) are not touched.
        """
        hook = cls.get(name)
        super().unregister(name)
        cls._global_hooks = [h for h in cls._global_hooks if h is not hook]

    @classmethod
    def register_global(cls, hook: "Hook") -> None:
        """Register a hook both in the name registry and in the global hook list."""
        cls.register(hook)
        cls._global_hooks.append(hook)

    @classmethod
    def get_global_by_type(cls, hook_type: object) -> "list[Hook]":
        """Return all globally-registered hooks matching hook_type, in order."""
        return cls.get_by_type(hook_type, cls._global_hooks)

    @classmethod
    async def fire(
        cls,
        hook_type: object,
        instance_hooks: list,
        /,
        *args: Any,
        _source_tags: frozenset = frozenset(),
        **kwargs: Any,
    ) -> None:
        """Fire *instance_hooks* then matching global hooks, deduplicating by identity.

        *instance_hooks* must already be filtered to the correct hook type by
        the caller — ``fire`` iterates them directly without re-filtering.
        Instance hooks run first; any global hook sharing identity with an
        already-fired instance hook is skipped, preventing double-firing when
        the same hook is registered both globally (``@hook``) and on the instance.

        ``_source_tags`` is consumed by ``fire`` and not forwarded to hooks.
        Global hooks with ``tags`` set only fire if the source has at least one
        matching tag (OR semantics). Hooks with ``tags=None`` always fire.
        """
        fired_ids: set[int] = set()
        for h in instance_hooks:
            await h(*args, **kwargs)
            fired_ids.add(id(h))
        for h in cls.get_global_by_type(hook_type):
            if id(h) not in fired_ids:
                hook_tags = getattr(h, "tags", None)
                if hook_tags is None or (_source_tags and hook_tags & _source_tags):
                    await h(*args, **kwargs)

    @classmethod
    def wrap(
        cls,
        fn: Callable[..., Any],
        hook_type: "HookType | list[HookType]",
        *,
        lock: bool = False,
        **fixed_kwargs: Any,
    ) -> "Hook" | Callable[..., Any]:
        """Wrap *fn* as a Hook for instance use, or return the existing wrapper.

        - If *fn* is already a Hook (has ``.metadata``), re-register and return it.
        - If the Hook registered under ``fn.__name__`` wraps this same *fn*,
          return that existing wrapper.
        - If the name is free, create a new Hook, register it (instance-scope
          only), and return it.
        - If the name is held by something else (e.g. a second closure from the
          same factory), return a new Hook WITHOUT registering it. It fires
          normally, but saving its owner raises ``UnserializableHookError``.

        Supports multi-type via a list, lock serialization, and fixed_kwargs injection.
        """
        if hasattr(fn, "metadata"):
            cls.register(fn)
            return fn

        name = getattr(fn, "__name__", None)
        existing = cls._registry.get(name) if name else None
        if existing is not None and getattr(existing, "fn", None) is fn:
            return existing

        from pygents.hooks import Hook

        types = hook_type if isinstance(hook_type, list) else [hook_type]
        stored_type = types[0] if len(types) == 1 else tuple(types)
        asyncio_lock = asyncio.Lock() if lock else None
        wrapper = Hook(fn, stored_type, asyncio_lock, fixed_kwargs)
        if existing is None:
            cls.register(wrapper)
        return wrapper

    @classmethod
    def get_by_type(cls, hook_type: object, hooks: "list[Any]") -> "list[Any]":
        """Return all hooks in the given list that match the hook_type, in order.

        The *hooks* list can contain either real ``Hook`` instances or plain
        callables with a ``.type`` attribute (as used in tests).
        """

        def matches(h: Any) -> bool:
            ht = getattr(h, "type", None)
            if ht is None:
                return False
            if isinstance(ht, (tuple, frozenset)):
                return any(hook_type is t for t in ht)
            return ht is hook_type

        return [h for h in hooks if matches(h)]
