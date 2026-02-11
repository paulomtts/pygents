"""
Tests for pygents.memory, driven by the following decision table.

Decision table for pygents/memory.py
------------------------------------
__init__(limit, hooks=None):
  I1  limit < 1 -> ValueError "limit must be >= 1"
  I2  limit >= 1 -> _items = deque(maxlen=limit); hooks = list(hooks) or []

limit/items properties: maxlen; list(_items).

append(*items):
  A1  BEFORE_APPEND hook -> current, result, await hook(current, result), replace _items from result, then append items
  A2  No BEFORE_APPEND -> append items (eviction by maxlen)
  A3  AFTER_APPEND hook -> await hook(list(self._items))

clear(): _items.clear().

branch(limit=None, hooks=...):
  B1  limit None -> child limit = self.limit
  B2  limit set -> child limit = that
  B3  hooks is ... -> child inherits self.hooks
  B4  hooks=[] or hooks=[...] -> child uses that list
  B5  Copy _items to child (smaller limit truncates when appending)

__len__, __iter__, __bool__, __repr__: delegate to _items / len.
to_dict: limit, items, hooks. from_dict: restore limit, items, hooks via HookRegistry.get.
"""

import asyncio

import pytest

from pygents.hooks import MemoryHook, hook
from pygents.memory import Memory


# -- construction -------------------------------------------------------------


def test_init_sets_limit():
    mem = Memory(5)
    assert mem.limit == 5
    assert len(mem) == 0


def test_init_with_empty_hooks():
    mem = Memory(5, hooks=[])
    assert mem.hooks == []
    assert mem.limit == 5


@pytest.mark.parametrize("invalid_limit", [0, -1, -3])
def test_init_rejects_limit_below_one(invalid_limit):
    with pytest.raises(ValueError, match="limit must be >= 1"):
        Memory(invalid_limit)


# -- append -------------------------------------------------------------------


def test_append_single_item():
    async def _():
        mem = Memory(3)
        await mem.append("a")
        assert mem.items == ["a"]

    asyncio.run(_())


def test_append_multiple_items():
    async def _():
        mem = Memory(5)
        await mem.append("a", "b", "c")
        assert mem.items == ["a", "b", "c"]

    asyncio.run(_())


def test_append_evicts_oldest_when_full():
    async def _():
        mem = Memory(3)
        await mem.append("a", "b", "c", "d")
        assert mem.items == ["b", "c", "d"]

    asyncio.run(_())


def test_append_successive_eviction():
    async def _():
        mem = Memory(2)
        await mem.append("a")
        await mem.append("b")
        await mem.append("c")
        assert mem.items == ["b", "c"]

    asyncio.run(_())


# -- BEFORE_APPEND hooks -----------------------------------------------------


def test_before_append_is_called_on_every_append():
    from pygents.registry import HookRegistry

    HookRegistry.clear()
    calls = []

    async def spy(items, result):
        calls.append(list(items))
        result.extend(items)

    spy.hook_type = MemoryHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = Memory(5, hooks=[spy])
        await mem.append("a")
        await mem.append("b")
        assert calls == [[], ["a"]]
        assert mem.items == ["a", "b"]

    asyncio.run(_())


def test_before_append_reduces_items():
    async def keep_last_two(items, result):
        result.extend(items[-2:] if len(items) >= 2 else items)

    keep_last_two.hook_type = MemoryHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = Memory(5, hooks=[keep_last_two])
        await mem.append("a", "b", "c", "d", "e")
        assert mem.items == ["a", "b", "c", "d", "e"]
        await mem.append("f")
        assert mem.items == ["d", "e", "f"]

    asyncio.run(_())


def test_before_append_result_subject_to_limit():
    async def four_items(items, result):
        result.extend(["x", "y", "w", "v"])

    four_items.hook_type = MemoryHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = Memory(3, hooks=[four_items])
        await mem.append("a")
        assert mem.items == ["w", "v", "a"]

    asyncio.run(_())


def test_before_append_can_return_empty():
    async def clear_all(items, result):
        pass

    clear_all.hook_type = MemoryHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = Memory(3, hooks=[clear_all])
        await mem.append("a", "b")
        assert mem.items == ["a", "b"]

    asyncio.run(_())


def test_before_append_receives_copy():
    from pygents.registry import HookRegistry

    HookRegistry.clear()

    async def mutating_compact(items, result):
        items.clear()
        result.append("x")

    mutating_compact.hook_type = MemoryHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = Memory(5, hooks=[mutating_compact])
        await mem.append("a", "b")
        assert mem.items == ["x", "a", "b"]

    asyncio.run(_())


def test_no_hooks_does_not_compact():
    async def _():
        mem = Memory(5)
        await mem.append("a", "b", "c")
        await mem.append("d")
        assert mem.items == ["a", "b", "c", "d"]

    asyncio.run(_())


def test_after_append_hook_called():
    seen = []

    async def after_spy(items):
        seen.append(list(items))

    after_spy.hook_type = MemoryHook.AFTER_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = Memory(5, hooks=[after_spy])
        await mem.append("a")
        assert seen == [["a"]]
        await mem.append("b", "c")
        assert seen == [["a"], ["a", "b", "c"]]

    asyncio.run(_())


# -- clear --------------------------------------------------------------------


def test_clear_empties_memory():
    async def _():
        mem = Memory(3)
        await mem.append("a", "b")
        mem.clear()
        assert len(mem) == 0
        assert mem.items == []

    asyncio.run(_())


# -- branch -------------------------------------------------------------------


def test_branch_inherits_items():
    async def _():
        mem = Memory(5)
        await mem.append("a", "b", "c")
        child = mem.branch()
        assert child.items == ["a", "b", "c"]
        assert child.limit == 5

    asyncio.run(_())


def test_branch_is_independent_from_parent():
    async def _():
        mem = Memory(5)
        await mem.append("a", "b")
        child = mem.branch()
        await child.append("c")
        await mem.append("x")
        assert mem.items == ["a", "b", "x"]
        assert child.items == ["a", "b", "c"]

    asyncio.run(_())


def test_branch_with_smaller_limit_truncates():
    async def _():
        mem = Memory(5)
        await mem.append("a", "b", "c", "d", "e")
        child = mem.branch(limit=3)
        assert child.limit == 3
        assert child.items == ["c", "d", "e"]

    asyncio.run(_())


def test_branch_with_larger_limit():
    async def _():
        mem = Memory(3)
        await mem.append("a", "b")
        child = mem.branch(limit=10)
        assert child.limit == 10
        assert child.items == ["a", "b"]

    asyncio.run(_())


def test_branch_of_empty_memory():
    async def _():
        mem = Memory(3)
        child = mem.branch()
        assert child.items == []
        assert child.limit == 3

    asyncio.run(_())


def test_nested_branch():
    async def _():
        root = Memory(5)
        await root.append("a")
        child = root.branch()
        await child.append("b")
        grandchild = child.branch()
        await grandchild.append("c")
        assert root.items == ["a"]
        assert child.items == ["a", "b"]
        assert grandchild.items == ["a", "b", "c"]

    asyncio.run(_())


def test_branch_inherits_hooks():
    from pygents.registry import HookRegistry

    HookRegistry.clear()
    calls = []

    async def spy(items, result):
        calls.append(list(items))
        result.extend(items)

    spy.hook_type = MemoryHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = Memory(5, hooks=[spy])
        await mem.append("a")
        child = mem.branch()
        await child.append("b")
        assert calls == [[], ["a"]]
        assert child.items == ["a", "b"]

    asyncio.run(_())


def test_branch_overrides_hooks():
    async def keep_last(items, result):
        result.extend(items[-1:] if items else [])

    keep_last.hook_type = MemoryHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = Memory(5, hooks=[keep_last])
        child = mem.branch(hooks=[])
        await child.append("a")
        await child.append("b")
        assert child.items == ["a", "b"]

    asyncio.run(_())


def test_branch_with_explicit_hooks_uses_them():
    async def parent_compact(items, result):
        result.extend(items[-1:] if items else [])

    async def child_compact(items, result):
        result.extend(items[-2:] if len(items) >= 2 else items)

    parent_compact.hook_type = MemoryHook.BEFORE_APPEND  # type: ignore[attr-defined]
    child_compact.hook_type = MemoryHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = Memory(5, hooks=[parent_compact])
        await mem.append("a", "b", "c")
        child = mem.branch(hooks=[child_compact])
        await child.append("d")
        assert child.items == ["b", "c", "d"]
        await mem.append("x")
        assert mem.items == ["c", "x"]

    asyncio.run(_())


# -- dunder protocols ---------------------------------------------------------


def test_len():
    async def _():
        mem = Memory(5)
        assert len(mem) == 0
        await mem.append("a", "b")
        assert len(mem) == 2

    asyncio.run(_())


def test_iter():
    async def _():
        mem = Memory(5)
        await mem.append("a", "b", "c")
        assert list(mem) == ["a", "b", "c"]

    asyncio.run(_())


def test_bool_empty():
    mem = Memory(3)
    assert not mem


def test_bool_non_empty():
    async def _():
        mem = Memory(3)
        await mem.append("a")
        assert mem

    asyncio.run(_())


def test_repr():
    async def _():
        mem = Memory(4)
        await mem.append("a", "b")
        assert repr(mem) == "Memory(limit=4, len=2)"

    asyncio.run(_())


# -- serialization ------------------------------------------------------------


def test_to_dict():
    async def _():
        mem = Memory(3)
        await mem.append("a", "b")
        assert mem.to_dict() == {"limit": 3, "items": ["a", "b"], "hooks": {}}

    asyncio.run(_())


def test_from_dict():
    data = {"limit": 4, "items": [1, 2, 3]}
    mem = Memory.from_dict(data)
    assert mem.limit == 4
    assert mem.items == [1, 2, 3]


def test_from_dict_empty_items():
    data = {"limit": 2}
    mem = Memory.from_dict(data)
    assert mem.limit == 2
    assert mem.items == []


def test_roundtrip():
    async def _():
        mem = Memory(5)
        await mem.append("a", "b", "c")
        restored = Memory.from_dict(mem.to_dict())
        assert restored.limit == mem.limit
        assert restored.items == mem.items

    asyncio.run(_())


def test_roundtrip_with_before_append_hook():
    from pygents.registry import HookRegistry

    HookRegistry.clear()

    @hook(MemoryHook.BEFORE_APPEND)
    async def keep_last_two(items, result):
        result.extend(items[-2:] if len(items) >= 2 else items)

    async def _():
        mem = Memory(5, hooks=[keep_last_two])
        await mem.append("a", "b", "c", "d", "e")
        await mem.append("f")
        assert mem.items == ["d", "e", "f"]
        restored = Memory.from_dict(mem.to_dict())
        assert restored.limit == mem.limit
        assert restored.items == mem.items
        await restored.append("g")
        assert restored.items == ["e", "f", "g"]

    asyncio.run(_())
    HookRegistry.clear()
