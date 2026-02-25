"""
Tests for ContextQueue, driven by the following decision table.

Decision table for ContextQueue
------------------------------------
__init__(limit):
  I1  limit < 1 -> ValueError "limit must be >= 1"
  I2  limit >= 1 -> _items = deque(maxlen=limit); hooks = []

limit/items properties: maxlen; list(_items).

append(*items):
  A1  Non-ContextItem -> TypeError
  A2  BEFORE_APPEND hook -> await hook(list(self._items)), then append items (eviction by maxlen)
  A3  No BEFORE_APPEND -> append items (eviction by maxlen)
  A4  AFTER_APPEND hook -> await hook(list(self._items))

clear(): _items.clear().

history(last=None):
  H1  last=None -> join all items' content as str, newline-separated
  H2  last=N -> join only the N most-recent items
  H3  last > len -> same as last=None (no error)
  H4  Empty queue -> empty string

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
from pygents.registry import HookRegistry


def _ci(content) -> ContextItem:
    return ContextItem(content=content)


# -- construction -------------------------------------------------------------


def test_init_sets_limit():
    mem = ContextQueue(5)
    assert mem.limit == 5
    assert len(mem) == 0


def test_init_with_empty_hooks():
    mem = ContextQueue(5)
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
            await mem.append("raw string")  # type: ignore[arg-type]

    asyncio.run(_())


def test_append_rejects_non_context_item_mixed():
    async def _():
        mem = ContextQueue(3)
        with pytest.raises(TypeError, match="ContextItem"):
            await mem.append(_ci("a"), "not an item")  # type: ignore[arg-type]

    asyncio.run(_())


def test_append_state_unchanged_on_partial_type_error():
    async def _():
        mem = ContextQueue(5)
        await mem.append(_ci("a"))
        assert len(mem) == 1
        with pytest.raises(TypeError):
            await mem.append(_ci("b"), "not-a-context-item")  # type: ignore[arg-type]
        assert len(mem) == 1

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
    HookRegistry.clear()
    calls = []

    async def spy(queue, incoming, current):
        calls.append((list(incoming), list(current)))

    spy.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5)
        mem.hooks.append(spy)  # type: ignore[arg-type]
        await mem.append(_ci("a"))
        await mem.append(_ci("b"))
        assert calls == [([_ci("a")], []), ([_ci("b")], [_ci("a")])]
        assert mem.items == [_ci("a"), _ci("b")]

    asyncio.run(_())


def test_before_append_mutation_of_snapshot_does_not_affect_queue():
    async def mutating(queue, incoming, current):
        current.clear()

    mutating.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5)
        mem.hooks.append(mutating)  # type: ignore[arg-type]
        await mem.append(_ci("a"), _ci("b"))
        assert mem.items == [_ci("a"), _ci("b")]

    asyncio.run(_())


def test_after_append_hook_called():
    seen = []

    async def after_spy(queue, incoming, current):
        seen.append((list(incoming), list(current)))

    after_spy.type = ContextQueueHook.AFTER_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5)
        mem.hooks.append(after_spy)  # type: ignore[arg-type]
        await mem.append(_ci("a"))
        assert seen == [([_ci("a")], [_ci("a")])]
        await mem.append(_ci("b"), _ci("c"))
        assert seen == [
            ([_ci("a")], [_ci("a")]),
            ([_ci("b"), _ci("c")], [_ci("a"), _ci("b"), _ci("c")]),
        ]

    asyncio.run(_())


# -- clear --------------------------------------------------------------------


def test_clear_empties_memory():
    async def _():
        mem = ContextQueue(3)
        await mem.append(_ci("a"), _ci("b"))
        await mem.clear()
        assert len(mem) == 0
        assert mem.items == []

    asyncio.run(_())


def test_before_clear_hook_fires_with_current_items():
    HookRegistry.clear()
    fired = []

    @hook(ContextQueueHook.BEFORE_CLEAR)
    async def before_clear(queue, items):
        fired.append(list(items))

    async def _():
        mem = ContextQueue(5)
        await mem.append(_ci("a"), _ci("b"))
        await mem.clear()
        assert fired == [[_ci("a"), _ci("b")]]
        assert len(mem) == 0

    asyncio.run(_())


def test_after_clear_hook_fires_with_empty_queue():
    HookRegistry.clear()
    fired = []

    @hook(ContextQueueHook.AFTER_CLEAR)
    async def after_clear(queue):
        fired.append(list(queue.items))

    async def _():
        mem = ContextQueue(5)
        await mem.append(_ci("a"))
        await mem.clear()
        assert fired == [[]]

    asyncio.run(_())


def test_on_evict_hook_fires_when_appending_to_full_queue():
    HookRegistry.clear()
    evicted = []

    @hook(ContextQueueHook.ON_EVICT)
    async def on_evict(queue, item):
        evicted.append(item)

    async def _():
        mem = ContextQueue(2)
        await mem.append(_ci("a"), _ci("b"))
        assert evicted == []
        await mem.append(_ci("c"))
        assert evicted == [_ci("a")]
        await mem.append(_ci("d"))
        assert evicted == [_ci("a"), _ci("b")]
        assert mem.items == [_ci("c"), _ci("d")]

    asyncio.run(_())


def test_on_evict_not_fired_when_queue_not_full():
    HookRegistry.clear()
    evicted = []

    @hook(ContextQueueHook.ON_EVICT)
    async def on_evict_nf(queue, item):
        evicted.append(item)

    async def _():
        mem = ContextQueue(5)
        await mem.append(_ci("a"), _ci("b"), _ci("c"))
        assert evicted == []

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
    HookRegistry.clear()
    calls = []

    async def spy(queue, incoming, current):
        calls.append((list(incoming), list(current)))

    spy.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5)
        mem.hooks.append(spy)  # type: ignore[arg-type]
        await mem.append(_ci("a"))
        child = mem.branch()
        await child.append(_ci("b"))
        assert calls == [([_ci("a")], []), ([_ci("b")], [_ci("a")])]
        assert child.items == [_ci("a"), _ci("b")]

    asyncio.run(_())


def test_branch_overrides_hooks():
    async def keep_last(queue, incoming, current):
        pass

    keep_last.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5)
        mem.hooks.append(keep_last)  # type: ignore[arg-type]
        child = mem.branch(hooks=[])
        await child.append(_ci("a"))
        await child.append(_ci("b"))
        assert child.items == [_ci("a"), _ci("b")]

    asyncio.run(_())


def test_branch_hooks_none_gives_empty_hooks():
    async def my_hook(queue, incoming, current):
        pass

    my_hook.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5)
        mem.hooks.append(my_hook)  # type: ignore[arg-type]
        child = mem.branch(hooks=None)
        assert child.hooks == []

    asyncio.run(_())


def test_branch_with_explicit_hooks_uses_them():
    async def parent_compact(queue, incoming, current):
        pass

    async def child_compact(queue, incoming, current):
        pass

    parent_compact.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]
    child_compact.type = ContextQueueHook.BEFORE_APPEND  # type: ignore[attr-defined]

    async def _():
        mem = ContextQueue(5)
        mem.hooks.append(parent_compact)  # type: ignore[arg-type]
        await mem.append(_ci("a"), _ci("b"), _ci("c"))
        child = mem.branch(hooks=[child_compact])  # type: ignore[arg-type]
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
            "tags": [],
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
    HookRegistry.clear()

    @hook(ContextQueueHook.BEFORE_APPEND)
    async def keep_last_two(queue, incoming, current):
        pass

    async def _():
        mem = ContextQueue(5)
        mem.hooks.append(keep_last_two)
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


def test_from_dict_restored_hooks_fire():
    HookRegistry.clear()
    fired = []

    @hook(ContextQueueHook.BEFORE_APPEND)
    async def cq_restored_hook(queue, incoming, current):
        fired.append((list(incoming), list(current)))

    mem = ContextQueue(5)
    mem.hooks.append(cq_restored_hook)
    data = mem.to_dict()
    assert "before_append" in data["hooks"]

    restored = ContextQueue.from_dict(data)
    assert len(restored.hooks) == 1

    async def _():
        await restored.append(_ci("a"))

    asyncio.run(_())
    assert fired == [([_ci("a")], [])]  # hook fired with incoming=["a"], current=[]


def test_items_setter_replaces_queue_contents():
    """Covers the `items` setter (lines 74-75 in context.py)."""

    async def _():
        q = ContextQueue(5)
        await q.append(_ci("a"), _ci("b"))
        q.items = [_ci("x"), _ci("y")]
        assert q.items == [_ci("x"), _ci("y")]

    asyncio.run(_())


# -- history ------------------------------------------------------------------


def test_history_all_items():
    async def _():
        q = ContextQueue(5)
        await q.append(_ci("a"), _ci("b"), _ci("c"))
        assert q.history() == "a\nb\nc"

    asyncio.run(_())


def test_history_last_n_items():
    async def _():
        q = ContextQueue(5)
        await q.append(_ci("a"), _ci("b"), _ci("c"), _ci("d"))
        assert q.history(last=2) == "c\nd"

    asyncio.run(_())


def test_history_last_exceeds_length():
    async def _():
        q = ContextQueue(5)
        await q.append(_ci("x"), _ci("y"))
        assert q.history(last=10) == "x\ny"

    asyncio.run(_())


def test_history_empty_queue():
    q = ContextQueue(5)
    assert q.history() == ""
    assert q.history(last=3) == ""


# ---------------------------------------------------------------------------
# ContextQueueHook queue-reference verification
# ---------------------------------------------------------------------------


def test_before_append_hook_receives_queue_as_first_arg():
    """BEFORE_APPEND hook receives the queue as first arg, enabling inspection."""
    HookRegistry.clear()
    received_queues = []

    @hook(ContextQueueHook.BEFORE_APPEND)
    async def capture_queue(queue, incoming, current):
        received_queues.append(queue)

    async def _():
        q = ContextQueue(5)
        await q.append(_ci("a"))
        assert received_queues == [q]
        assert received_queues[0].limit == 5

    asyncio.run(_())


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def test_context_queue_tags_default_empty_frozenset():
    q = ContextQueue(5)
    assert q.tags == frozenset()


def test_context_queue_tags_stored_as_frozenset():
    q = ContextQueue(5, tags=["p", "q"])
    assert q.tags == frozenset({"p", "q"})


def test_context_queue_global_hook_with_tag_fires_only_for_matching_queue():
    HookRegistry.clear()
    fired = []

    @hook(ContextQueueHook.BEFORE_APPEND, tags={"monitored"})
    async def tagged_cq_hook(queue, incoming, current):
        fired.append("fired")

    async def _():
        tagged = ContextQueue(5, tags=["monitored"])
        untagged = ContextQueue(5)
        await tagged.append(_ci("a"))
        await untagged.append(_ci("b"))
        assert fired == ["fired"]

    asyncio.run(_())


def test_context_queue_global_hook_without_tag_fires_for_all_queues():
    HookRegistry.clear()
    fired = []

    @hook(ContextQueueHook.BEFORE_APPEND)
    async def untagged_cq_hook(queue, incoming, current):
        fired.append("fired")

    async def _():
        tagged = ContextQueue(5, tags=["x"])
        untagged = ContextQueue(5)
        await tagged.append(_ci("a"))
        await untagged.append(_ci("b"))
        assert len(fired) == 2

    asyncio.run(_())


def test_context_queue_tags_survive_serialization_roundtrip():
    async def _():
        q = ContextQueue(5, tags=["mem", "fast"])
        await q.append(_ci("a"))
        data = q.to_dict()
        assert set(data["tags"]) == {"mem", "fast"}
        restored = ContextQueue.from_dict(data)
        assert restored.tags == frozenset({"mem", "fast"})

    asyncio.run(_())


def test_context_queue_branch_copies_tags():
    async def _():
        q = ContextQueue(5, tags=["env:test"])
        child = q.branch()
        assert child.tags == frozenset({"env:test"})

    asyncio.run(_())
