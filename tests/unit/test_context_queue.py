"""
Tests for ContextQueue, driven by the following decision table.

Decision table for ContextQueue
------------------------------------
__init__(limit, hooks=None):
  I1  limit < 1 -> ValueError "limit must be >= 1"
  I2  limit >= 1 -> _items = deque(maxlen=limit); hooks = list(hooks) or []

limit/items properties: maxlen; list(_items).

append(*items):
  A1  Non-ContextItem -> TypeError
  A2  BEFORE_APPEND hook -> await hook(list(self._items)), then append items (eviction by maxlen)
  A3  No BEFORE_APPEND -> append items (eviction by maxlen)
  A4  AFTER_APPEND hook -> await hook(list(self._items))

clear(): _items.clear().

branch(limit=None, hooks=...):
  B1  limit None -> child limit = self.limit
  B2  limit set -> child limit = that
  B3  hooks is ... -> child inherits self.hooks
  B4  hooks=[] or hooks=[...] -> child uses that list
  B5  Copy _items to child (smaller limit truncates when appending)

__len__, __iter__, __bool__, __repr__: delegate to _items / len.
to_dict: limit, items (as ContextItem dicts), hooks.
from_dict: restore limit, items (via ContextItem.from_dict), hooks via HookRegistry.get.
"""

import asyncio

import pytest

from pygents.context import ContextItem, ContextQueue
from pygents.hooks import ContextQueueHook, hook


def _ci(content) -> ContextItem:
    return ContextItem(content=content)


# -- construction -------------------------------------------------------------


def test_init_sets_limit():
    mem = ContextQueue(5)
    assert mem.limit == 5
    assert len(mem) == 0


def test_init_with_empty_hooks():
    mem = ContextQueue(5, hooks=[])
    assert mem.hooks == []
    assert mem.limit == 5


@pytest.mark.parametrize("invalid_limit", [0, -1, -3])
def test_init_rejects_limit_below_one(invalid_limit):
    with pytest.raises(ValueError, match="limit must be >= 1"):
        ContextQueue(invalid_limit)


# -- append -------------------------------------------------------------------


def test_append_rejects_non_context_item():
    async def _():
        mem = ContextQueue(3)
        with pytest.raises(TypeError, match="ContextItem"):
            await mem.append("raw string")

    asyncio.run(_())


def test_append_rejects_non_context_item_mixed():
    async def _():
        mem = ContextQueue(3)
        with pytest.raises(TypeError, match="ContextItem"):
            await mem.append(_ci("a"), "not an item")

    asyncio.run(_())


def test_append_single_item():
    async def _():
        mem = ContextQueue(3)
        await mem.append(_ci("a"))
        assert mem.items == [_ci("a")]

    asyncio.run(_())


def test_append_multiple_items():
    async def _():
        mem = ContextQueue(5)
        await mem.append(_ci("a"), _ci("b"), _ci("c"))
        assert mem.items == [_ci("a"), _ci("b"), _ci("c")]

    asyncio.run(_())


def test_append_evicts_oldest_when_full():
    async def _():
        mem = ContextQueue(3)
        await mem.append(_ci("a"), _ci("b"), _ci("c"), _ci("d"))
        assert mem.items == [_ci("b"), _ci("c"), _ci("d")]

    asyncio.run(_())


def test_append_successive_eviction():
    async def _():
        mem = ContextQueue(2)
        await mem.append(_ci("a"))
        await mem.append(_ci("b"))
        await mem.append(_ci("c"))
        assert mem.items == [_ci("b"), _ci("c")]

    asyncio.run(_())


# -- BEFORE_APPEND hooks -----------------------------------------------------


def test_before_append_is_called_on_every_append():
    from pygents.registry import HookRegistry

    HookRegistry.clear()
    calls = []

    async def spy(items):
        calls.append(list(items))

    spy.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5, hooks=[spy])
        await mem.append(_ci("a"))
        await mem.append(_ci("b"))
        assert calls == [[], [_ci("a")]]
        assert mem.items == [_ci("a"), _ci("b")]

    asyncio.run(_())


def test_before_append_does_not_replace_items():
    async def keep_last_two(items):
        pass

    keep_last_two.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5, hooks=[keep_last_two])
        await mem.append(_ci("a"), _ci("b"), _ci("c"), _ci("d"), _ci("e"))
        assert mem.items == [_ci("a"), _ci("b"), _ci("c"), _ci("d"), _ci("e")]
        await mem.append(_ci("f"))
        assert mem.items == [_ci("b"), _ci("c"), _ci("d"), _ci("e"), _ci("f")]

    asyncio.run(_())


def test_before_append_observes_only():
    async def four_items(items):
        pass

    four_items.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(3, hooks=[four_items])
        await mem.append(_ci("a"))
        assert mem.items == [_ci("a")]

    asyncio.run(_())


def test_before_append_can_be_no_op():
    async def clear_all(items):
        pass

    clear_all.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(3, hooks=[clear_all])
        await mem.append(_ci("a"), _ci("b"))
        assert mem.items == [_ci("a"), _ci("b")]

    asyncio.run(_())


def test_before_append_receives_copy():
    from pygents.registry import HookRegistry

    HookRegistry.clear()

    async def mutating_compact(items):
        items.clear()

    mutating_compact.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5, hooks=[mutating_compact])
        await mem.append(_ci("a"), _ci("b"))
        assert mem.items == [_ci("a"), _ci("b")]

    asyncio.run(_())


def test_no_hooks_does_not_compact():
    async def _():
        mem = ContextQueue(5)
        await mem.append(_ci("a"), _ci("b"), _ci("c"))
        await mem.append(_ci("d"))
        assert mem.items == [_ci("a"), _ci("b"), _ci("c"), _ci("d")]

    asyncio.run(_())


def test_after_append_hook_called():
    seen = []

    async def after_spy(items):
        seen.append(list(items))

    after_spy.type = ContextQueueHook.AFTER_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5, hooks=[after_spy])
        await mem.append(_ci("a"))
        assert seen == [[_ci("a")]]
        await mem.append(_ci("b"), _ci("c"))
        assert seen == [[_ci("a")], [_ci("a"), _ci("b"), _ci("c")]]

    asyncio.run(_())


# -- clear --------------------------------------------------------------------


def test_clear_empties_memory():
    async def _():
        mem = ContextQueue(3)
        await mem.append(_ci("a"), _ci("b"))
        mem.clear()
        assert len(mem) == 0
        assert mem.items == []

    asyncio.run(_())


# -- branch -------------------------------------------------------------------


def test_branch_inherits_items():
    async def _():
        mem = ContextQueue(5)
        await mem.append(_ci("a"), _ci("b"), _ci("c"))
        child = mem.branch()
        assert child.items == [_ci("a"), _ci("b"), _ci("c")]
        assert child.limit == 5

    asyncio.run(_())


def test_branch_is_independent_from_parent():
    async def _():
        mem = ContextQueue(5)
        await mem.append(_ci("a"), _ci("b"))
        child = mem.branch()
        await child.append(_ci("c"))
        await mem.append(_ci("x"))
        assert mem.items == [_ci("a"), _ci("b"), _ci("x")]
        assert child.items == [_ci("a"), _ci("b"), _ci("c")]

    asyncio.run(_())


def test_branch_with_smaller_limit_truncates():
    async def _():
        mem = ContextQueue(5)
        await mem.append(_ci("a"), _ci("b"), _ci("c"), _ci("d"), _ci("e"))
        child = mem.branch(limit=3)
        assert child.limit == 3
        assert child.items == [_ci("c"), _ci("d"), _ci("e")]

    asyncio.run(_())


def test_branch_with_larger_limit():
    async def _():
        mem = ContextQueue(3)
        await mem.append(_ci("a"), _ci("b"))
        child = mem.branch(limit=10)
        assert child.limit == 10
        assert child.items == [_ci("a"), _ci("b")]

    asyncio.run(_())


def test_branch_of_empty_memory():
    async def _():
        mem = ContextQueue(3)
        child = mem.branch()
        assert child.items == []
        assert child.limit == 3

    asyncio.run(_())


def test_nested_branch():
    async def _():
        root = ContextQueue(5)
        await root.append(_ci("a"))
        child = root.branch()
        await child.append(_ci("b"))
        grandchild = child.branch()
        await grandchild.append(_ci("c"))
        assert root.items == [_ci("a")]
        assert child.items == [_ci("a"), _ci("b")]
        assert grandchild.items == [_ci("a"), _ci("b"), _ci("c")]

    asyncio.run(_())


def test_branch_inherits_hooks():
    from pygents.registry import HookRegistry

    HookRegistry.clear()
    calls = []

    async def spy(items):
        calls.append(list(items))

    spy.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5, hooks=[spy])
        await mem.append(_ci("a"))
        child = mem.branch()
        await child.append(_ci("b"))
        assert calls == [[], [_ci("a")]]
        assert child.items == [_ci("a"), _ci("b")]

    asyncio.run(_())


def test_branch_overrides_hooks():
    async def keep_last(items):
        pass

    keep_last.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5, hooks=[keep_last])
        child = mem.branch(hooks=[])
        await child.append(_ci("a"))
        await child.append(_ci("b"))
        assert child.items == [_ci("a"), _ci("b")]

    asyncio.run(_())


def test_branch_with_explicit_hooks_uses_them():
    async def parent_compact(items):
        pass

    async def child_compact(items):
        pass

    parent_compact.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]
    child_compact.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5, hooks=[parent_compact])
        await mem.append(_ci("a"), _ci("b"), _ci("c"))
        child = mem.branch(hooks=[child_compact])
        await child.append(_ci("d"))
        assert child.items == [_ci("a"), _ci("b"), _ci("c"), _ci("d")]
        await mem.append(_ci("x"))
        assert mem.items == [_ci("a"), _ci("b"), _ci("c"), _ci("x")]

    asyncio.run(_())


# -- dunder protocols ---------------------------------------------------------


def test_len():
    async def _():
        mem = ContextQueue(5)
        assert len(mem) == 0
        await mem.append(_ci("a"), _ci("b"))
        assert len(mem) == 2

    asyncio.run(_())


def test_iter():
    async def _():
        mem = ContextQueue(5)
        await mem.append(_ci("a"), _ci("b"), _ci("c"))
        assert list(mem) == [_ci("a"), _ci("b"), _ci("c")]

    asyncio.run(_())


def test_bool_empty():
    mem = ContextQueue(3)
    assert not mem


def test_bool_non_empty():
    async def _():
        mem = ContextQueue(3)
        await mem.append(_ci("a"))
        assert mem

    asyncio.run(_())


def test_repr():
    async def _():
        mem = ContextQueue(4)
        await mem.append(_ci("a"), _ci("b"))
        assert repr(mem) == "ContextQueue(limit=4, len=2)"

    asyncio.run(_())


# -- serialization ------------------------------------------------------------


def test_to_dict():
    async def _():
        mem = ContextQueue(3)
        await mem.append(_ci("a"), _ci("b"))
        assert mem.to_dict() == {
            "limit": 3,
            "items": [
                {"id": None, "description": None, "content": "a"},
                {"id": None, "description": None, "content": "b"},
            ],
            "hooks": {},
        }

    asyncio.run(_())


def test_from_dict():
    data = {
        "limit": 4,
        "items": [
            {"id": None, "description": None, "content": 1},
            {"id": None, "description": None, "content": 2},
            {"id": None, "description": None, "content": 3},
        ],
    }
    mem = ContextQueue.from_dict(data)
    assert mem.limit == 4
    assert mem.items == [_ci(1), _ci(2), _ci(3)]


def test_from_dict_empty_items():
    data = {"limit": 2}
    mem = ContextQueue.from_dict(data)
    assert mem.limit == 2
    assert mem.items == []


def test_roundtrip():
    async def _():
        mem = ContextQueue(5)
        await mem.append(_ci("a"), _ci("b"), _ci("c"))
        restored = ContextQueue.from_dict(mem.to_dict())
        assert restored.limit == mem.limit
        assert restored.items == mem.items

    asyncio.run(_())


def test_roundtrip_with_before_append_hook():
    from pygents.registry import HookRegistry

    HookRegistry.clear()

    @hook(ContextQueueHook.BEFORE_APPEND)
    async def keep_last_two(items):
        pass

    async def _():
        mem = ContextQueue(5, hooks=[keep_last_two])
        await mem.append(_ci("a"), _ci("b"), _ci("c"), _ci("d"), _ci("e"))
        await mem.append(_ci("f"))
        assert mem.items == [_ci("b"), _ci("c"), _ci("d"), _ci("e"), _ci("f")]
        restored = ContextQueue.from_dict(mem.to_dict())
        assert restored.limit == mem.limit
        assert restored.items == mem.items
        await restored.append(_ci("g"))
        assert restored.items == [_ci("c"), _ci("d"), _ci("e"), _ci("f"), _ci("g")]

    asyncio.run(_())
    HookRegistry.clear()
