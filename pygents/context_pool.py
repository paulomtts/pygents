from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from pygents.hooks import Hook


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
    hooks : list[Hook] | None
        Lifecycle hooks to attach to this pool.
    """

    def __init__(self, limit: int | None = None, hooks: list["Hook"] | None = None) -> None:
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        self._items: dict[str, ContextItem[Any]] = {}
        self._limit = limit
        self.hooks: list[Hook] = list(hooks) if hooks else []

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
            f"- [{item.id}] {item.description}"
            for item in self._items.values()
        )

    # -- hooks ----------------------------------------------------------------

    async def _run_hook(self, hook_type: Any, *args: Any) -> None:
        from pygents.registry import HookRegistry
        if h := HookRegistry.get_by_type(hook_type, self.hooks):
            await h(self, *args)

    # -- mutation -------------------------------------------------------------

    async def add(self, item: ContextItem[Any]) -> None:
        from pygents.hooks import ContextPoolHook
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
            del self._items[oldest_key]
        await self._run_hook(ContextPoolHook.BEFORE_ADD, item)
        self._items[item.id] = item
        await self._run_hook(ContextPoolHook.AFTER_ADD, item)

    def get(self, id: str) -> ContextItem[Any]:
        return self._items[id]

    async def remove(self, id: str) -> None:
        from pygents.hooks import ContextPoolHook
        item = self._items[id]          # raises KeyError early if missing
        await self._run_hook(ContextPoolHook.BEFORE_REMOVE, item)
        del self._items[id]
        await self._run_hook(ContextPoolHook.AFTER_REMOVE, item)

    async def clear(self) -> None:
        from pygents.hooks import ContextPoolHook
        await self._run_hook(ContextPoolHook.BEFORE_CLEAR)
        self._items.clear()
        await self._run_hook(ContextPoolHook.AFTER_CLEAR)

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
        child = type(self)(limit=child_limit, hooks=list(self.hooks))
        for item in self.items:
            # Replicate eviction logic inline — no hooks, no async overhead
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
        from pygents.utils import serialize_hooks_by_type
        return {
            "limit": self._limit,
            "items": [item.to_dict() for item in self._items.values()],
            "hooks": serialize_hooks_by_type(self.hooks),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextPool":
        from pygents.utils import rebuild_hooks_from_serialization
        pool = cls(limit=data.get("limit"))
        for item_data in data.get("items", []):
            item = ContextItem.from_dict(item_data)
            pool._items[item.id] = item   # bypass add() to avoid hooks
        pool.hooks = rebuild_hooks_from_serialization(data.get("hooks", {}))
        return pool
