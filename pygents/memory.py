from __future__ import annotations

from collections import deque
from typing import Any, Iterator

from pygents.hooks import Hook, MemoryHook
from pygents.registry import HookRegistry
from pygents.utils import rebuild_hooks_from_serialization, serialize_hooks_by_type


class Memory:
    """
    A bounded, branchable memory window.

    Intended as a building block for agent memory (working, semantic,
    episodic, procedural, etc.). Holds context items in a fixed-size window;
    supports branching so child scopes inherit the parent's state and
    diverge independently.

    Parameters
    ----------
    limit : int
        Maximum number of items the window can hold. When a new item is
        appended and the window is full, the oldest item is evicted.
    hooks : list[Hook] | None
        Optional list of hooks (BEFORE_APPEND and/or AFTER_APPEND). Each
        must have type set (e.g. via @hook(MemoryHook.BEFORE_APPEND)).
    """

    def __init__(
        self,
        limit: int,
        hooks: list[Hook] | None = None,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self._items: deque[Any] = deque(maxlen=limit)
        self.hooks: list[Hook] = list(hooks) if hooks else []

    # -- properties ----------------------------------------------------------

    @property
    def limit(self) -> int:
        return self._items.maxlen  # type: ignore[return-value]

    @property
    def items(self) -> list[Any]:
        return list(self._items)

    @items.setter
    def items(self, items: list[Any]) -> None:
        self._items.clear()
        self._items.extend(items)

    # -- mutation -------------------------------------------------------------

    async def append(self, *items: Any) -> None:
        """Add one or more items. Oldest items are evicted when full.

        BEFORE_APPEND hooks are run with (items,); then new items
        are appended; then AFTER_APPEND hooks are run with (items,).
        """
        if before_append_hook := HookRegistry.get_by_type(
            MemoryHook.BEFORE_APPEND, self.hooks
        ):
            await before_append_hook(list(self._items))
        for item in items:
            self._items.append(item)
        if after_append_hook := HookRegistry.get_by_type(
            MemoryHook.AFTER_APPEND, self.hooks
        ):
            await after_append_hook(list(self._items))

    def clear(self) -> None:
        self._items.clear()

    # -- branching ------------------------------------------------------------

    def branch(
        self,
        limit: int | None = None,
        hooks: list[Hook] | None = ...,  # type: ignore[assignment]
    ) -> Memory:
        """
        Create a child memory that starts with a snapshot of this
        memory's current state.

        The child is fully independent. By default the child inherits
        this memory's hooks; pass hooks=[] or a new list to override.
        """
        child_limit = limit if limit is not None else self.limit
        child_hooks = (
            self.hooks if hooks is ... else (hooks if hooks is not None else [])
        )
        child = Memory(child_limit, hooks=child_hooks)
        for item in self._items:
            child._items.append(item)
        return child

    # -- dunder protocols -----------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._items)

    def __bool__(self) -> bool:
        return len(self._items) > 0

    def __repr__(self) -> str:
        return f"Memory(limit={self.limit}, len={len(self)})"

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "limit": self.limit,
            "items": list(self._items),
            "hooks": serialize_hooks_by_type(self.hooks),
        }
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Memory:
        memory = cls(limit=data["limit"])
        for item in data.get("items", []):
            memory._items.append(item)
        memory.hooks = rebuild_hooks_from_serialization(data.get("hooks", {}))
        return memory
