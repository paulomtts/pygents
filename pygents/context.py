from __future__ import annotations

from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from pygents.hooks import (
    ContextPoolHook,
    ContextQueueHook,
    Hook,
)
from pygents.registry import HookRegistry
from pygents.utils import (
    build_method_decorator,
    rebuild_hooks_from_serialization,
    serialize_hooks_by_type,
)


@dataclass(frozen=True)
class ContextItem[T]:
    content: T
    description: str | None = None
    id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextItem[Any]:
        return cls(
            content=data["content"],
            description=data.get("description"),
            id=data.get("id"),
        )


class ContextQueue:
    """
    A bounded, branchable memory window.

    Intended as a building block for agent memory (working, semantic,
    episodic, etc.). Holds context items in a fixed-size window;
    supports branching so child scopes inherit the parent's state and
    diverge independently.

    Parameters
    ----------
    limit : int
        Maximum number of items the window can hold. When a new item is
        appended and the window is full, the oldest item is evicted.
    """

    def __init__(
        self,
        limit: int,
        tags: list[str] | frozenset[str] | None = None,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self._items: deque[ContextItem[Any]] = deque(maxlen=limit)
        self.tags: frozenset[str] = frozenset(tags or [])
        self.hooks: list[Hook] = []

    # -- properties ----------------------------------------------------------

    @property
    def limit(self) -> int:
        return self._items.maxlen  # type: ignore[return-value]

    @property
    def items(self) -> list[ContextItem[Any]]:
        return list(self._items)

    @items.setter
    def items(self, items: list[ContextItem[Any]]) -> None:
        self._items.clear()
        self._items.extend(items)

    # -- hook decorators ------------------------------------------------------

    def before_append(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires before items are inserted.

        Parameters
        ----------
        fn : async (queue: ContextQueue, incoming: list[ContextItem], current: list[ContextItem]) -> None
            Receives the queue, items about to be appended, and a snapshot of
            the current queue contents.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """

        return build_method_decorator(
            ContextQueueHook.BEFORE_APPEND, self.hooks, fn, lock, fixed_kwargs
        )

    def after_append(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after items are inserted.

        Parameters
        ----------
        fn : async (queue: ContextQueue, appended: list[ContextItem], current: list[ContextItem]) -> None
            Receives the queue, items that were appended, and a snapshot of the
            queue contents after insertion.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ContextQueueHook.AFTER_APPEND, self.hooks, fn, lock, fixed_kwargs
        )

    def before_clear(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires before the queue is cleared.

        Parameters
        ----------
        fn : async (queue: ContextQueue, items: list[ContextItem]) -> None
            Receives the queue and a snapshot of its contents before clearing.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ContextQueueHook.BEFORE_CLEAR, self.hooks, fn, lock, fixed_kwargs
        )

    def after_clear(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after the queue is cleared.

        Parameters
        ----------
        fn : async (queue: ContextQueue) -> None
            Receives the now-empty queue.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ContextQueueHook.AFTER_CLEAR, self.hooks, fn, lock, fixed_kwargs
        )

    def on_evict(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires when the oldest item is evicted to make room.

        Parameters
        ----------
        fn : async (queue: ContextQueue, evicted: ContextItem) -> None
            Receives the queue and the item being evicted.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ContextQueueHook.ON_EVICT, self.hooks, fn, lock, fixed_kwargs
        )

    # -- mutation -------------------------------------------------------------

    async def _run_hooks(self, hook_type: Any, *args: Any) -> None:
        await HookRegistry.fire(
            hook_type, HookRegistry.get_by_type(hook_type, self.hooks), *args,
            _source_tags=self.tags
        )

    async def append(self, *items: ContextItem[Any]) -> None:
        """Add one or more ContextItems. Oldest items are evicted when full.

        Raises TypeError if any item is not a ContextItem. BEFORE_APPEND
        hooks are run with (items,); then new items are appended; then
        AFTER_APPEND hooks are run with (items,).
        """
        for item in items:
            if not isinstance(item, ContextItem):
                raise TypeError(
                    f"ContextQueue only accepts ContextItem instances, got {type(item).__name__!r}"
                )
        await self._run_hooks(
            ContextQueueHook.BEFORE_APPEND, self, list(items), list(self._items)
        )
        for item in items:
            if len(self._items) == self.limit:
                evicted = self._items[0]
                await self._run_hooks(ContextQueueHook.ON_EVICT, self, evicted)
            self._items.append(item)
        await self._run_hooks(
            ContextQueueHook.AFTER_APPEND, self, list(items), list(self._items)
        )

    def history(self, last: int | None = None) -> str:
        """Return the queue contents as a newline-joined string.

        Parameters
        ----------
        last:
            If given, only the *last* N items are included.
            If ``None`` (default), all items are included.
        """
        items = self.items
        if last is not None:
            items = self.items[-last:]
        return "\n".join(str(item.content) for item in items)

    async def clear(self) -> None:
        await self._run_hooks(ContextQueueHook.BEFORE_CLEAR, self, list(self._items))
        self._items.clear()
        await self._run_hooks(ContextQueueHook.AFTER_CLEAR, self)

    # -- branching ------------------------------------------------------------

    def branch(
        self,
        limit: int | None = None,
        hooks: list[Hook] | None = ...,  # type: ignore[assignment]
    ) -> ContextQueue:
        """
        Create a child context queue that starts with a snapshot of this
        context queue's current state.

        The child is fully independent. By default the child inherits
        this context queue's hooks; pass hooks=[] or a new list to override.
        """
        child_limit = limit if limit is not None else self.limit
        child_hooks = (
            self.hooks if hooks is ... else (hooks if hooks is not None else [])
        )
        child = ContextQueue(child_limit, tags=self.tags)
        child.hooks = list(child_hooks)
        for item in self._items:
            child._items.append(item)
        return child

    # -- dunder protocols -----------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[ContextItem[Any]]:
        return iter(self._items)

    def __bool__(self) -> bool:
        return len(self._items) > 0

    def __repr__(self) -> str:
        return f"ContextQueue(limit={self.limit}, len={len(self)})"

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "items": [item.to_dict() for item in self._items],
            "hooks": serialize_hooks_by_type(self.hooks),
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextQueue:
        cq = cls(limit=data["limit"], tags=data.get("tags", []))
        for raw in data.get("items", []):
            cq._items.append(ContextItem.from_dict(raw))
        cq.hooks = rebuild_hooks_from_serialization(data.get("hooks", {}))
        return cq


class ContextPool:
    """
    A collection of context items, optionally bounded by size.

    Intended as a building block for agent context. Holds context in a
    dictionary keyed by id. When a limit is set and the pool is full,
    the oldest-inserted item is evicted on each new addition.

    Parameters
    ----------
    limit : int | None
        Maximum number of items the pool can hold. When a new item is added
        and the pool is full, the oldest item (by insertion order) is evicted.
        ``None`` means unbounded.
    """

    def __init__(self, limit: int | None = None, tags: list[str] | frozenset[str] | None = None) -> None:
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        self._items: dict[str, ContextItem[Any]] = {}
        self._limit = limit
        self.tags: frozenset[str] = frozenset(tags or [])
        self.hooks: list[Hook] = []

    # -- properties -----------------------------------------------------------

    @property
    def limit(self) -> int | None:
        return self._limit

    @property
    def items(self) -> list[ContextItem[Any]]:
        return list(self._items.values())

    def catalogue(self) -> str:
        """Return a formatted string of id–description pairs, one per line.

        Each line has the form ``- [id] description``. Useful for building
        LLM selection prompts without repeating the join pattern everywhere.
        Returns an empty string when the pool is empty.
        """
        return "\n".join(
            f"- [{item.id}] {item.description}" for item in self._items.values()
        )

    # -- hook decorators ------------------------------------------------------

    def before_add(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires before an item is inserted into the pool.

        Parameters
        ----------
        fn : async (pool: ContextPool, item: ContextItem) -> None
            Receives the pool instance and the item about to be added.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """

        return build_method_decorator(
            ContextPoolHook.BEFORE_ADD, self.hooks, fn, lock, fixed_kwargs
        )

    def after_add(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after an item is inserted into the pool.

        Parameters
        ----------
        fn : async (pool: ContextPool, item: ContextItem) -> None
            Receives the pool instance and the item that was added.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ContextPoolHook.AFTER_ADD, self.hooks, fn, lock, fixed_kwargs
        )

    def before_remove(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires before an item is removed from the pool.

        Parameters
        ----------
        fn : async (pool: ContextPool, item: ContextItem) -> None
            Receives the pool instance and the item about to be removed.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ContextPoolHook.BEFORE_REMOVE, self.hooks, fn, lock, fixed_kwargs
        )

    def after_remove(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after an item is removed from the pool.

        Parameters
        ----------
        fn : async (pool: ContextPool, item: ContextItem) -> None
            Receives the pool instance and the item that was removed.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ContextPoolHook.AFTER_REMOVE, self.hooks, fn, lock, fixed_kwargs
        )

    def before_clear(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires before the pool is cleared.

        Parameters
        ----------
        fn : async (pool: ContextPool, snapshot: dict[str, ContextItem]) -> None
            Receives the pool and a snapshot of its items taken before clearing.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ContextPoolHook.BEFORE_CLEAR, self.hooks, fn, lock, fixed_kwargs
        )

    def after_clear(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires after the pool is cleared.

        Parameters
        ----------
        fn : async (pool: ContextPool) -> None
            Receives the pool instance.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ContextPoolHook.AFTER_CLEAR, self.hooks, fn, lock, fixed_kwargs
        )

    def on_evict(
        self, fn: Any = None, *, lock: bool = False, **fixed_kwargs: Any
    ) -> Any:
        """Fires when the oldest item is evicted to make room.

        Parameters
        ----------
        fn : async (pool: ContextPool, item: ContextItem) -> None
            Receives the pool instance and the item being evicted.
        lock : bool, optional
            If True, concurrent calls are serialized with an asyncio.Lock.
        **fixed_kwargs
            Fixed keyword arguments merged into every invocation.
        """
        return build_method_decorator(
            ContextPoolHook.ON_EVICT, self.hooks, fn, lock, fixed_kwargs
        )

    async def _run_hooks(self, hook_type: Any, *args: Any) -> None:
        await HookRegistry.fire(
            hook_type, HookRegistry.get_by_type(hook_type, self.hooks), self, *args,
            _source_tags=self.tags
        )

    # -- mutation -------------------------------------------------------------

    async def add(self, item: ContextItem[Any]) -> None:
        if item.id is None or item.description is None:
            raise ValueError(
                "ContextPool requires a ContextItem with both 'id' and 'description' set"
            )
        if (
            self._limit is not None
            and item.id not in self._items
            and len(self._items) >= self._limit
        ):
            oldest_key = next(iter(self._items))
            await self._run_hooks(ContextPoolHook.ON_EVICT, self._items[oldest_key])
            del self._items[oldest_key]
        await self._run_hooks(ContextPoolHook.BEFORE_ADD, item)
        self._items[item.id] = item
        await self._run_hooks(ContextPoolHook.AFTER_ADD, item)

    def get(self, id: str) -> ContextItem[Any]:
        return self._items[id]

    async def remove(self, id: str) -> None:
        item = self._items[id]  # raises KeyError early if missing
        await self._run_hooks(ContextPoolHook.BEFORE_REMOVE, item)
        del self._items[id]
        await self._run_hooks(ContextPoolHook.AFTER_REMOVE, item)

    async def clear(self) -> None:
        await self._run_hooks(ContextPoolHook.BEFORE_CLEAR, dict(self._items))
        self._items.clear()
        await self._run_hooks(ContextPoolHook.AFTER_CLEAR)

    # -- branching ------------------------------------------------------------

    def branch(self, limit: int | None = ...) -> "ContextPool":  # type: ignore[assignment]
        """
        Create a child context pool that starts with a snapshot of this
        context pool's current state.

        The child is fully independent. By default the child inherits this
        pool's limit; pass a different value to override. Parent hooks are
        copied to the child; no hooks fire during the snapshot copy.
        """
        child_limit = self._limit if limit is ... else limit
        child = type(self)(limit=child_limit, tags=self.tags)
        child.hooks = list(self.hooks)
        for item in self.items:
            if item.id is None:
                raise ValueError("ContextPool requires a ContextItem with 'id' set")
            # ? REASON: Replicate eviction logic inline — no hooks, no async overhead
            if (
                child._limit is not None
                and item.id not in child._items
                and len(child._items) >= child._limit
            ):
                oldest_key = next(iter(child._items))
                del child._items[oldest_key]
            child._items[item.id] = item
        return child

    # -- dunder protocols -----------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[ContextItem[Any]]:
        return iter(self._items.values())

    def __bool__(self) -> bool:
        return bool(self._items)

    def __repr__(self) -> str:
        return f"ContextPool(limit={self._limit}, len={len(self)})"

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self._limit,
            "items": [item.to_dict() for item in self._items.values()],
            "hooks": serialize_hooks_by_type(self.hooks),
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextPool":
        pool = cls(limit=data.get("limit"), tags=data.get("tags", []))
        for item_data in data.get("items", []):
            item = ContextItem.from_dict(item_data)
            if item.id is None:
                raise ValueError("ContextPool requires a ContextItem with 'id' set")
            pool._items[item.id] = item  # ? REASON: bypass add() to avoid hooks
        pool.hooks = rebuild_hooks_from_serialization(data.get("hooks", {}))
        return pool


_current_context_queue: ContextVar["ContextQueue | None"] = ContextVar(
    "pygents.context_queue", default=None
)
_current_context_pool: ContextVar["ContextPool | None"] = ContextVar(
    "pygents.context_pool", default=None
)
