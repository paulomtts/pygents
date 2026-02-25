"""
Tests for pygents.context_pool (ContextItem and ContextPool).

Decision table
--------------
ContextItem:
  P1  to_dict/from_dict: roundtrip preserves id, description, content

ContextPool.__init__:
  I1  limit=None -> unbounded
  I2  limit >= 1 -> bounded
  I3  limit < 1 -> ValueError

add():
  A1  No limit: items added freely
  A2  At limit, new id: evict oldest, add new
  A3  At limit, existing id: update in place, no eviction

get():
  G1  Existing id -> returns ContextItem
  G2  Missing id -> KeyError

remove():
  R1  Existing id -> removes it
  R2  Missing id -> KeyError

clear():
  C1  Empties pool

branch():
  B1  No args -> child inherits limit and items snapshot
  B2  branch(limit=N) smaller -> child evicts oldest to fit
  B3  Child is independent (mutations don't affect parent)
  BT1 branch() returns type(self) not hardcoded ContextPool

catalogue():
  CA1  Empty pool -> empty string
  CA2  Items present -> one "- [id] description" line per item
  CA3  Line order matches insertion order

Dunders:
  D1  __len__ -> item count
  D2  __iter__ -> all items
  D3  __bool__ -> False when empty, True otherwise
  D4  __repr__ -> includes limit and len

to_dict/from_dict:
  S1  limit=None roundtrip
  S2  limit=N roundtrip with items
  S3  from_dict restores exact state, bypassing eviction

ContextPoolHook:
  H1  BEFORE_ADD(pool, item) fires before item inserted
  H2  AFTER_ADD(pool, item) fires after item inserted
  H3  BEFORE_REMOVE(pool, item) fires before item deleted
  H4  AFTER_REMOVE(pool, item) fires after item deleted
  H5  BEFORE_CLEAR(pool) fires before items cleared
  H6  AFTER_CLEAR(pool) fires after items cleared
  H7  hooks passed in __init__ stored on instance
  H8  branch() copies parent hooks to child
  H9  to_dict/from_dict roundtrip preserves hooks
"""

import asyncio

import pytest

from pygents.context import ContextItem, ContextPool
from pygents.hooks import ContextPoolHook, hook
from pygents.registry import HookRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(id: str, content=None) -> ContextItem:
    return ContextItem(content=content or id, description=f"desc-{id}", id=id)


# ---------------------------------------------------------------------------
# P1 – ContextItem to_dict / from_dict
# ---------------------------------------------------------------------------


def test_pool_item_to_dict_from_dict_roundtrip():
    item = ContextItem(id="x", description="some desc", content={"nested": 42})
    data = item.to_dict()
    restored = ContextItem.from_dict(data)
    assert restored.id == item.id
    assert restored.description == item.description
    assert restored.content == item.content


# ---------------------------------------------------------------------------
# I1-I3 – ContextPool.__init__
# ---------------------------------------------------------------------------


def test_context_pool_default_no_limit():
    pool = ContextPool()
    assert pool.limit is None


def test_context_pool_limit_set():
    pool = ContextPool(limit=3)
    assert pool.limit == 3


def test_context_pool_limit_below_one_raises():
    with pytest.raises(ValueError, match="limit must be >= 1"):
        ContextPool(limit=0)
    with pytest.raises(ValueError, match="limit must be >= 1"):
        ContextPool(limit=-5)


# ---------------------------------------------------------------------------
# A1-A3 – add()
# ---------------------------------------------------------------------------


def test_context_pool_add_rejects_item_with_none_id():
    pool = ContextPool()
    item = ContextItem(content="x", description="desc")
    with pytest.raises(ValueError, match="'id' and 'description'"):
        asyncio.run(pool.add(item))


def test_context_pool_add_rejects_item_with_none_description():
    pool = ContextPool()
    item = ContextItem(content="x", id="key")
    with pytest.raises(ValueError, match="'id' and 'description'"):
        asyncio.run(pool.add(item))


def test_add_no_limit_grows_freely():
    pool = ContextPool()
    for i in range(10):
        asyncio.run(pool.add(_item(str(i))))
    assert len(pool) == 10


def test_add_at_limit_evicts_oldest():
    pool = ContextPool(limit=2)
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    asyncio.run(pool.add(_item("c")))
    assert len(pool) == 2
    with pytest.raises(KeyError):
        pool.get("a")
    assert pool.get("b").id == "b"
    assert pool.get("c").id == "c"


def test_add_existing_id_updates_no_eviction():
    pool = ContextPool(limit=2)
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    updated = ContextItem(id="a", description="updated", content="new_content")
    asyncio.run(pool.add(updated))
    assert len(pool) == 2
    assert pool.get("a").content == "new_content"
    assert pool.get("b").id == "b"


# ---------------------------------------------------------------------------
# G1-G2 – get()
# ---------------------------------------------------------------------------


def test_get_existing_id():
    pool = ContextPool()
    asyncio.run(pool.add(_item("z")))
    result = pool.get("z")
    assert result.id == "z"


def test_get_missing_id_raises():
    pool = ContextPool()
    with pytest.raises(KeyError):
        pool.get("nonexistent")


# ---------------------------------------------------------------------------
# R1-R2 – remove()
# ---------------------------------------------------------------------------


def test_remove_existing_id():
    pool = ContextPool()
    asyncio.run(pool.add(_item("r")))
    asyncio.run(pool.remove("r"))
    assert len(pool) == 0


def test_remove_missing_id_raises():
    pool = ContextPool()
    with pytest.raises(KeyError):
        asyncio.run(pool.remove("ghost"))


# ---------------------------------------------------------------------------
# C1 – clear()
# ---------------------------------------------------------------------------


def test_clear_empties_pool():
    pool = ContextPool()
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    asyncio.run(pool.clear())
    assert len(pool) == 0


# ---------------------------------------------------------------------------
# B1-B3 – branch()
# ---------------------------------------------------------------------------


def test_branch_inherits_limit_and_items():
    pool = ContextPool(limit=5)
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    child = pool.branch()
    assert child.limit == 5
    assert len(child) == 2
    assert child.get("a").id == "a"
    assert child.get("b").id == "b"


def test_branch_smaller_limit_evicts_oldest():
    pool = ContextPool()
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    asyncio.run(pool.add(_item("c")))
    child = pool.branch(limit=2)
    assert child.limit == 2
    assert len(child) == 2
    with pytest.raises(KeyError):
        child.get("a")
    assert child.get("b").id == "b"
    assert child.get("c").id == "c"


def test_branch_limit_none_removes_limit_from_child():
    bounded = ContextPool(limit=2)
    asyncio.run(bounded.add(_item("a")))
    child = bounded.branch(limit=None)
    assert child.limit is None
    # child can exceed the parent's limit without eviction
    asyncio.run(child.add(_item("b")))
    asyncio.run(child.add(_item("c")))
    asyncio.run(child.add(_item("d")))
    assert len(child) == 4


def test_branch_returns_same_type_for_subclass():
    class MyPool(ContextPool):
        pass

    pool = MyPool()
    asyncio.run(pool.add(_item("a")))
    child = pool.branch()
    assert type(child) is MyPool


def test_branch_child_is_independent():
    pool = ContextPool()
    asyncio.run(pool.add(_item("a")))
    child = pool.branch()
    asyncio.run(child.add(_item("b")))
    asyncio.run(pool.add(_item("c")))
    assert len(pool) == 2
    assert len(child) == 2
    with pytest.raises(KeyError):
        pool.get("b")
    with pytest.raises(KeyError):
        child.get("c")


# ---------------------------------------------------------------------------
# D1-D4 – dunders
# ---------------------------------------------------------------------------


def test_len():
    pool = ContextPool()
    assert len(pool) == 0
    asyncio.run(pool.add(_item("x")))
    assert len(pool) == 1


def test_iter():
    pool = ContextPool()
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    ids = [item.id for item in pool]
    assert ids == ["a", "b"]


def test_bool():
    pool = ContextPool()
    assert not pool
    asyncio.run(pool.add(_item("x")))
    assert pool


def test_repr():
    pool = ContextPool(limit=3)
    asyncio.run(pool.add(_item("a")))
    r = repr(pool)
    assert "3" in r
    assert "1" in r


# ---------------------------------------------------------------------------
# S1-S3 – to_dict / from_dict
# ---------------------------------------------------------------------------


def test_to_dict_from_dict_no_limit():
    pool = ContextPool()
    asyncio.run(pool.add(_item("a")))
    data = pool.to_dict()
    assert data["limit"] is None
    assert len(data["items"]) == 1
    restored = ContextPool.from_dict(data)
    assert restored.limit is None
    assert len(restored) == 1
    assert restored.get("a").id == "a"


def test_to_dict_from_dict_with_limit_and_items():
    pool = ContextPool(limit=3)
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    data = pool.to_dict()
    assert data["limit"] == 3
    restored = ContextPool.from_dict(data)
    assert restored.limit == 3
    assert len(restored) == 2
    assert restored.get("a").id == "a"
    assert restored.get("b").id == "b"


def test_from_dict_restores_exact_state_bypasses_eviction():
    # from_dict should restore items directly without triggering eviction,
    # even if the item count matches the limit exactly
    data = {
        "limit": 2,
        "items": [
            {"id": "a", "description": "d", "content": 1},
            {"id": "b", "description": "d", "content": 2},
        ],
        "hooks": {},
    }
    pool = ContextPool.from_dict(data)
    assert len(pool) == 2
    assert pool.get("a").content == 1
    assert pool.get("b").content == 2


def test_from_dict_raises_when_item_missing_id():
    data = {
        "limit": 2,
        "items": [
            {"content": 1, "description": "d"},
        ],
        "hooks": {},
    }
    with pytest.raises(ValueError, match="ContextPool requires a ContextItem with 'id' set"):
        ContextPool.from_dict(data)


# ---------------------------------------------------------------------------
# H1–H9 – ContextPoolHook
# ---------------------------------------------------------------------------


def test_before_add_fires_before_insertion():
    HookRegistry.clear()
    seen_in_pool = []

    @hook(ContextPoolHook.BEFORE_ADD)
    async def before_add(pool, item):
        seen_in_pool.append(item.id in pool._items)

    pool = ContextPool()
    asyncio.run(pool.add(_item("x")))
    assert seen_in_pool == [False]  # item not yet inserted


def test_after_add_fires_after_insertion():
    HookRegistry.clear()
    seen_in_pool = []

    @hook(ContextPoolHook.AFTER_ADD)
    async def after_add(pool, item):
        seen_in_pool.append(item.id in pool._items)

    pool = ContextPool()
    asyncio.run(pool.add(_item("y")))
    assert seen_in_pool == [True]  # item already inserted


def test_before_remove_fires_with_item_still_present():
    HookRegistry.clear()
    seen_in_pool = []

    @hook(ContextPoolHook.BEFORE_REMOVE)
    async def before_remove(pool, item):
        seen_in_pool.append(item.id in pool._items)

    pool = ContextPool()
    asyncio.run(pool.add(_item("r")))
    asyncio.run(pool.remove("r"))
    assert seen_in_pool == [True]  # item still present before removal


def test_after_remove_fires_with_item_gone():
    HookRegistry.clear()
    seen_in_pool = []

    @hook(ContextPoolHook.AFTER_REMOVE)
    async def after_remove(pool, item):
        seen_in_pool.append(item.id in pool._items)

    pool = ContextPool()
    asyncio.run(pool.add(_item("r")))
    asyncio.run(pool.remove("r"))
    assert seen_in_pool == [False]  # item gone after removal


def test_before_clear_fires():
    HookRegistry.clear()
    fired = []

    @hook(ContextPoolHook.BEFORE_CLEAR)
    async def before_clear(pool, snapshot):
        fired.append(len(snapshot))

    pool = ContextPool()
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    asyncio.run(pool.clear())
    assert fired == [2]  # snapshot had 2 items before clear


def test_after_clear_fires_with_empty_pool():
    HookRegistry.clear()
    fired = []

    @hook(ContextPoolHook.AFTER_CLEAR)
    async def after_clear(pool):
        fired.append(len(pool))

    pool = ContextPool()
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.clear())
    assert fired == [0]  # pool empty after clear


def test_hooks_passed_in_init_stored_on_instance():
    HookRegistry.clear()

    @hook(ContextPoolHook.BEFORE_ADD)
    async def my_hook(pool, item):
        pass

    pool = ContextPool()
    pool.hooks.append(my_hook)
    assert my_hook in pool.hooks


def test_branch_inherits_hooks():
    HookRegistry.clear()

    @hook(ContextPoolHook.AFTER_ADD)
    async def parent_hook(pool, item):
        pass

    pool = ContextPool()
    pool.hooks.append(parent_hook)
    child = pool.branch()
    assert parent_hook in child.hooks
    # child has a copy, not the same list
    assert child.hooks is not pool.hooks


# ---------------------------------------------------------------------------
# CA1–CA3 – catalogue()
# ---------------------------------------------------------------------------


def test_catalogue_empty_pool_returns_empty_string():
    pool = ContextPool()
    assert pool.catalogue() == ""


def test_catalogue_returns_id_description_lines():
    pool = ContextPool()
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    lines = pool.catalogue().splitlines()
    assert lines[0] == "- [a] desc-a"
    assert lines[1] == "- [b] desc-b"


def test_catalogue_order_matches_insertion_order():
    pool = ContextPool()
    for ch in ["x", "y", "z"]:
        asyncio.run(pool.add(_item(ch)))
    ids = [line.split("]")[0][3:] for line in pool.catalogue().splitlines()]
    assert ids == ["x", "y", "z"]


def test_context_pool_hooks_to_dict_from_dict_roundtrip():
    HookRegistry.clear()

    @hook(ContextPoolHook.BEFORE_ADD)
    async def roundtrip_hook(pool, item):
        pass

    pool = ContextPool()
    pool.hooks.append(roundtrip_hook)
    asyncio.run(pool.add(_item("a")))
    data = pool.to_dict()
    assert "before_add" in data["hooks"]
    assert data["hooks"]["before_add"] == ["roundtrip_hook"]

    restored = ContextPool.from_dict(data)
    assert len(restored.hooks) == 1
    assert restored.hooks[0] is roundtrip_hook


def test_from_dict_restored_hooks_fire():
    HookRegistry.clear()
    fired = []

    @hook(ContextPoolHook.BEFORE_ADD)
    async def cp_restored_hook(pool, item):
        fired.append(item.id)

    pool = ContextPool()
    pool.hooks.append(cp_restored_hook)
    data = pool.to_dict()
    assert "before_add" in data["hooks"]

    restored = ContextPool.from_dict(data)
    assert len(restored.hooks) == 1

    asyncio.run(restored.add(_item("z")))
    assert fired == ["z"]


# ---------------------------------------------------------------------------
# ON_EVICT hook
# ---------------------------------------------------------------------------


def test_on_evict_hook_fires_when_pool_at_limit():
    HookRegistry.clear()
    evicted = []

    @hook(ContextPoolHook.ON_EVICT)
    async def on_evict(pool, item):
        evicted.append(item.id)

    pool = ContextPool(limit=2)
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    assert evicted == []
    asyncio.run(pool.add(_item("c")))
    assert evicted == ["a"]
    asyncio.run(pool.add(_item("d")))
    assert evicted == ["a", "b"]


def test_on_evict_not_fired_when_pool_not_full():
    HookRegistry.clear()
    evicted = []

    @hook(ContextPoolHook.ON_EVICT)
    async def on_evict_nf(pool, item):
        evicted.append(item.id)

    pool = ContextPool(limit=5)
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    assert evicted == []


def test_on_evict_not_fired_when_updating_existing_id():
    HookRegistry.clear()
    evicted = []

    @hook(ContextPoolHook.ON_EVICT)
    async def on_evict_upd(pool, item):
        evicted.append(item.id)

    pool = ContextPool(limit=2)
    asyncio.run(pool.add(_item("a")))
    asyncio.run(pool.add(_item("b")))
    # Update "a" in place — no eviction expected
    updated = ContextItem(id="a", description="updated", content="new")
    asyncio.run(pool.add(updated))
    assert evicted == []
    assert pool.get("a").content == "new"


# ---------------------------------------------------------------------------
# ContextPoolHook.BEFORE_CLEAR snapshot verification
# ---------------------------------------------------------------------------


def test_before_clear_snapshot_is_non_empty_dict_taken_before_clear():
    """BEFORE_CLEAR hook receives a snapshot dict of items before they are cleared."""
    HookRegistry.clear()
    snapshots = []

    @hook(ContextPoolHook.BEFORE_CLEAR)
    async def capture_snapshot(pool, snapshot):
        snapshots.append(dict(snapshot))

    pool = ContextPool()
    asyncio.run(pool.add(_item("x")))
    asyncio.run(pool.add(_item("y")))
    asyncio.run(pool.clear())

    assert len(snapshots) == 1
    assert set(snapshots[0].keys()) == {"x", "y"}
    assert len(pool) == 0  # pool itself was cleared


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def test_context_pool_tags_default_empty_frozenset():
    pool = ContextPool()
    assert pool.tags == frozenset()


def test_context_pool_tags_stored_as_frozenset():
    pool = ContextPool(tags=["r", "s"])
    assert pool.tags == frozenset({"r", "s"})


def test_context_pool_global_hook_with_tag_fires_only_for_matching_pool():
    HookRegistry.clear()
    fired = []

    @hook(ContextPoolHook.BEFORE_ADD, tags={"audited"})
    async def tagged_cp_hook(pool, item):
        fired.append("fired")

    tagged = ContextPool(tags=["audited"])
    untagged = ContextPool()
    asyncio.run(tagged.add(_item("a")))
    asyncio.run(untagged.add(_item("b")))
    assert fired == ["fired"]


def test_context_pool_global_hook_without_tag_fires_for_all_pools():
    HookRegistry.clear()
    fired = []

    @hook(ContextPoolHook.BEFORE_ADD)
    async def untagged_cp_hook(pool, item):
        fired.append("fired")

    tagged = ContextPool(tags=["x"])
    untagged = ContextPool()
    asyncio.run(tagged.add(_item("a")))
    asyncio.run(untagged.add(_item("b")))
    assert len(fired) == 2


def test_context_pool_tags_survive_serialization_roundtrip():
    pool = ContextPool(tags=["store", "cache"])
    asyncio.run(pool.add(_item("a")))
    data = pool.to_dict()
    assert set(data["tags"]) == {"store", "cache"}
    restored = ContextPool.from_dict(data)
    assert restored.tags == frozenset({"store", "cache"})


def test_context_pool_branch_copies_tags():
    pool = ContextPool(tags=["env:staging"])
    asyncio.run(pool.add(_item("x")))
    child = pool.branch()
    assert child.tags == frozenset({"env:staging"})
