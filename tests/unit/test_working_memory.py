import pytest

from pygents.memory.working_memory import WorkingMemory


# -- construction -------------------------------------------------------------


def test_init_sets_limit():
    mem = WorkingMemory(5)
    assert mem.limit == 5
    assert len(mem) == 0


def test_init_rejects_zero_limit():
    with pytest.raises(ValueError, match="limit must be >= 1"):
        WorkingMemory(0)


def test_init_rejects_negative_limit():
    with pytest.raises(ValueError, match="limit must be >= 1"):
        WorkingMemory(-3)


# -- append -------------------------------------------------------------------


def test_append_single_item():
    mem = WorkingMemory(3)
    mem.append("a")
    assert mem.items == ["a"]


def test_append_multiple_items():
    mem = WorkingMemory(5)
    mem.append("a", "b", "c")
    assert mem.items == ["a", "b", "c"]


def test_append_evicts_oldest_when_full():
    mem = WorkingMemory(3)
    mem.append("a", "b", "c", "d")
    assert mem.items == ["b", "c", "d"]


def test_append_successive_eviction():
    mem = WorkingMemory(2)
    mem.append("a")
    mem.append("b")
    mem.append("c")
    assert mem.items == ["b", "c"]


# -- append with compact ------------------------------------------------------


def test_compact_is_called_before_new_items():
    mem = WorkingMemory(5)
    mem.append("a", "b", "c")

    calls = []

    def spy(items):
        calls.append(list(items))
        return items

    mem.append("d", compact=spy)
    assert calls == [["a", "b", "c"]]
    assert mem.items == ["a", "b", "c", "d"]


def test_compact_reduces_items():
    mem = WorkingMemory(5)
    mem.append("a", "b", "c", "d", "e")

    mem.append("f", compact=lambda items: items[-2:])
    assert mem.items == ["d", "e", "f"]


def test_compact_result_subject_to_limit():
    mem = WorkingMemory(3)
    mem.append("a")

    mem.append("z", compact=lambda _: ["x", "y", "w", "v"])
    # deque(maxlen=3) keeps only the last 3 of the compacted list, then appends "z"
    assert mem.items == ["w", "v", "z"]


def test_compact_can_return_empty():
    mem = WorkingMemory(3)
    mem.append("a", "b")

    mem.append("c", compact=lambda _: [])
    assert mem.items == ["c"]


def test_compact_receives_copy():
    """Mutating the list passed to compact must not affect the memory."""
    mem = WorkingMemory(5)
    mem.append("a", "b")

    def mutating_compact(items):
        items.clear()
        return ["x"]

    mem.append("y", compact=mutating_compact)
    # compact received a copy, so clearing it is harmless; result is ["x", "y"]
    assert mem.items == ["x", "y"]


def test_append_without_compact_does_not_compact():
    mem = WorkingMemory(5)
    mem.append("a", "b", "c")
    mem.append("d")
    assert mem.items == ["a", "b", "c", "d"]


# -- clear --------------------------------------------------------------------


def test_clear_empties_memory():
    mem = WorkingMemory(3)
    mem.append("a", "b")
    mem.clear()
    assert len(mem) == 0
    assert mem.items == []


# -- branch -------------------------------------------------------------------


def test_branch_inherits_items():
    mem = WorkingMemory(5)
    mem.append("a", "b", "c")
    child = mem.branch()
    assert child.items == ["a", "b", "c"]
    assert child.limit == 5


def test_branch_is_independent_from_parent():
    mem = WorkingMemory(5)
    mem.append("a", "b")
    child = mem.branch()

    child.append("c")
    mem.append("x")

    assert mem.items == ["a", "b", "x"]
    assert child.items == ["a", "b", "c"]


def test_branch_with_smaller_limit_truncates():
    mem = WorkingMemory(5)
    mem.append("a", "b", "c", "d", "e")
    child = mem.branch(limit=3)
    assert child.limit == 3
    assert child.items == ["c", "d", "e"]


def test_branch_with_larger_limit():
    mem = WorkingMemory(3)
    mem.append("a", "b")
    child = mem.branch(limit=10)
    assert child.limit == 10
    assert child.items == ["a", "b"]


def test_branch_of_empty_memory():
    mem = WorkingMemory(3)
    child = mem.branch()
    assert child.items == []
    assert child.limit == 3


def test_nested_branch():
    root = WorkingMemory(5)
    root.append("a")

    child = root.branch()
    child.append("b")

    grandchild = child.branch()
    grandchild.append("c")

    assert root.items == ["a"]
    assert child.items == ["a", "b"]
    assert grandchild.items == ["a", "b", "c"]


# -- dunder protocols ---------------------------------------------------------


def test_len():
    mem = WorkingMemory(5)
    assert len(mem) == 0
    mem.append("a", "b")
    assert len(mem) == 2


def test_iter():
    mem = WorkingMemory(5)
    mem.append("a", "b", "c")
    assert list(mem) == ["a", "b", "c"]


def test_bool_empty():
    mem = WorkingMemory(3)
    assert not mem


def test_bool_non_empty():
    mem = WorkingMemory(3)
    mem.append("a")
    assert mem


def test_repr():
    mem = WorkingMemory(4)
    mem.append("a", "b")
    assert repr(mem) == "WorkingMemory(limit=4, len=2)"


# -- serialization ------------------------------------------------------------


def test_to_dict():
    mem = WorkingMemory(3)
    mem.append("a", "b")
    assert mem.to_dict() == {"limit": 3, "items": ["a", "b"]}


def test_from_dict():
    data = {"limit": 4, "items": [1, 2, 3]}
    mem = WorkingMemory.from_dict(data)
    assert mem.limit == 4
    assert mem.items == [1, 2, 3]


def test_from_dict_empty_items():
    data = {"limit": 2}
    mem = WorkingMemory.from_dict(data)
    assert mem.limit == 2
    assert mem.items == []


def test_roundtrip():
    mem = WorkingMemory(5)
    mem.append("a", "b", "c")
    restored = WorkingMemory.from_dict(mem.to_dict())
    assert restored.limit == mem.limit
    assert restored.items == mem.items
