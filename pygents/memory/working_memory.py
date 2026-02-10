from __future__ import annotations

from collections import deque
from typing import Any, Callable, Iterator


class WorkingMemory:
    """
    A bounded, branchable working memory window.

    Holds current-turn context (messages, tool outputs, intermediate state)
    within a fixed-size window. Supports branching so that child scopes
    (e.g. a Turn branching from an Agent, or a tool branching from a Turn)
    inherit the parent's state and diverge independently.

    Parameters
    ----------
    limit : int
        Maximum number of items the window can hold.  When a new item is
        appended and the window is full, the oldest item is evicted.
    compact : (list[Any]) -> list[Any] | None
        If provided, called with the current items *before* new ones are
        inserted on every ``append`` call.  Must return the compacted list
        that will replace the window contents.  The returned list is still
        subject to the window limit.
    """

    def __init__(
        self,
        limit: int,
        compact: Callable[[list[Any]], list[Any]] | None = None,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self._items: deque[Any] = deque(maxlen=limit)
        self._compact = compact

    # -- properties ----------------------------------------------------------

    @property
    def limit(self) -> int:
        return self._items.maxlen  # type: ignore[return-value]

    @property
    def items(self) -> list[Any]:
        return list(self._items)

    # -- mutation -------------------------------------------------------------

    def append(self, *items: Any) -> None:
        """Add one or more items.  Oldest items are evicted when full.

        If a ``compact`` callback was provided at construction, it is called
        with the current items before the new ones are inserted.
        """
        if self._compact is not None:
            compacted = self._compact(list(self._items))
            self._items.clear()
            for item in compacted:
                self._items.append(item)
        for item in items:
            self._items.append(item)

    def clear(self) -> None:
        self._items.clear()

    # -- branching ------------------------------------------------------------

    def branch(
        self,
        limit: int | None = None,
        compact: Callable[[list[Any]], list[Any]] | None = ...,  # type: ignore[assignment]
    ) -> WorkingMemory:
        """
        Create a child working memory that starts with a snapshot of this
        memory's current state.

        The child is fully independent — appending to or clearing it does not
        affect the parent and vice-versa.

        Parameters
        ----------
        limit : int | None
            Window size for the child.  Defaults to the parent's limit.
            If smaller than the parent's, only the most recent items that
            fit are kept.
        compact : (list[Any]) -> list[Any] | None | ...
            Compaction callback for the child.  Defaults to the parent's
            callback.  Pass ``None`` explicitly to disable compaction on
            the child.
        """
        child_limit = limit if limit is not None else self.limit
        child_compact = self._compact if compact is ... else compact
        child = WorkingMemory(child_limit, compact=child_compact)
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
        return f"WorkingMemory(limit={self.limit}, len={len(self)})"

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "items": list(self._items),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkingMemory:
        memory = cls(limit=data["limit"])
        for item in data.get("items", []):
            memory._items.append(item)
        return memory
