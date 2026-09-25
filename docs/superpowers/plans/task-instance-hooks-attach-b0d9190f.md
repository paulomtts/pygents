<!-- task-pipeline: validated -->
# Subtask b0d9190f — Instance hooks attach freely; unsaveable ones refuse to save

Parent story: b5ea3988 (Closure hooks), milestone 909eb7ad. Narrows decision L3 of `docs/superpowers/specs/2026-09-25-lifecycle-fixes-design.md` and implements plan Task 2.1 of `docs/superpowers/plans/2026-09-25-lifecycle-fixes.md`. This is the story's only subtask.

## Scope

Files changed: `pygents/errors.py`, `pygents/__init__.py`, `pygents/registry.py` (`HookRegistry.wrap` only), `pygents/utils.py` (`serialize_hooks_by_type` only). Tests: `tests/unit/test_hooks.py`, `tests/unit/test_utils.py`.

1. **New error.** `class UnserializableHookError(ValueError)` in `pygents/errors.py`, imported in `pygents/__init__.py` and listed in `__all__` next to `UnregisteredHookError` and the other errors.
2. **`HookRegistry.wrap(fn, hook_type, *, lock=False, **fixed_kwargs)`** (the instance-decorator path, e.g. `@agent.after_turn`) no longer raises when `fn.__name__` is already taken. It builds the `Hook` as today, then:
   - name free → register it and return it (unchanged);
   - name held by a `Hook` wrapping the same `fn` → return that existing hook (the existing short-circuit, unchanged);
   - name held by anything else (a different function with the same name, e.g. a second closure from the same factory) → return the new `Hook` **without registering it**, no exception. The registry entry keeps pointing at the first hook.
   - The `hasattr(fn, "metadata")` branch (fn is already a Hook) is not part of this change.
3. **`serialize_hooks_by_type(hooks)`**: for every hook it serializes, with `name = getattr(h, "__name__", "hook")`, require `HookRegistry._registry.get(name) is h`. If not (nothing registered under that name, or a different object is — a closure or duplicate), raise `UnserializableHookError(f"hook {name!r} is not the one registered under that name (a closure or a duplicate); define it at module level to save its owner")`. Output format for serializable hooks is unchanged. Hooks with no `type` are still skipped (before the check). Every owner that serializes hooks through this function (e.g. `Agent.to_dict()`) therefore refuses to save when it holds an unregistered hook.
   - **This tightens an existing invariant, so three pre-existing tests in `tests/unit/test_utils.py` must be updated as part of this subtask** (they are already in scope per the Files-changed list above): `test_serialize_hooks_by_type_uses_enum_value_and_name` and `test_serialize_hooks_by_type_name_fallback` currently pass plain ad-hoc objects that are never registered in `HookRegistry`, and today's `serialize_hooks_by_type` serializes them anyway. Under the new check both would start raising `UnserializableHookError`. Fix `test_serialize_hooks_by_type_uses_enum_value_and_name` by registering `hook1` in `HookRegistry` (via `HookRegistry.register(hook1)`, cleared before/after) before asserting the unchanged dict output. Fix `test_serialize_hooks_by_type_name_fallback` by asserting it now raises `UnserializableHookError` instead of returning a dict — a hook with no `__name__` can never be the object registered under the fallback key `"hook"` (registration itself requires a real `__name__`), so this path is now always an error, not a successful fallback. `test_serialize_hooks_by_type_skips_hook_without_type` is unaffected (hooks with no `type` are skipped before the registry check).
4. **Unchanged:** the global `hook(...)` decorator and `HookRegistry.register_global` still go through `BaseRegistry.register`, so two different global hooks with one name still raise plain `ValueError(f"{name!r} already registered")`; re-registering the identical object is still a no-op (`_allow_reregister`).

## Observable behavior

- Two agents built by one factory that defines `@agent.after_turn async def log_turn(...)` inside it both construct without error. Each fires only its own closure. `one.to_dict()` succeeds (its hook is the registered one); `two.to_dict()` raises `UnserializableHookError` whose message contains `'log_turn'`.
- A module-level function used as an instance hook is the registered object, so its owner round-trips through `to_dict()` / `from_dict()` and the restored owner holds that same hook object.
- One module-level hook attached to two agents: `wrap` returns the same registered Hook for both, so both agents save.
- Error path: `UnserializableHookError` is a `ValueError` subclass, so existing `except ValueError` callers still catch it; the only place it is raised is `serialize_hooks_by_type`.

## Tests

Test-placement rule: design spec section 5 ("Testing", line 56) — "Everything in tests/unit (the existing layout)"; `tests/integration/` is reserved for multi-component wiring flows (per `tests/integration/test_agent_integration.py` docstring). These changes are isolated to the registry and the serializer, so every test below is **unit tier**; nothing goes in `tests/integration/`.

All are plain `def test_...()`; async bodies wrapped in `asyncio.run(...)` (no pytest-asyncio, no `async def` tests). Each test clears `HookRegistry` / `AgentRegistry` (and `ToolRegistry` if it defines tools) itself, since the conftest autouse fixture only clears `_global_hooks`.

| Test | File (tier) | Asserts |
|---|---|---|
| `test_two_agents_from_one_factory_both_construct_and_the_second_refuses_to_save` | `tests/unit/test_hooks.py` (unit) | Verbatim from plan Task 2.1: `make("one")`, `make("two")` raise nothing; `one.to_dict()` succeeds; `two.to_dict()` raises `UnserializableHookError` with `match="log_turn"`. |
| `test_both_factory_agents_fire_their_own_hook` | `tests/unit/test_hooks.py` (unit) | Run each factory agent once (via `asyncio.run`); each closure records only its own agent. |
| `test_a_module_level_instance_hook_still_round_trips` | `tests/unit/test_hooks.py` (unit) | Module-level function attached via instance decorator; `to_dict()` → `from_dict()` gives an agent whose hook is the same object. |
| `test_one_module_level_hook_on_two_agents_saves_twice` | `tests/unit/test_hooks.py` (unit) | Same module-level hook on two agents; both `to_dict()` calls succeed (plan Review Focus 4). |
| `test_two_global_hooks_with_one_name_still_raise` | `tests/unit/test_hooks.py` (unit) | Two distinct `@hook(Type)` functions sharing a name → second raises `ValueError` (and not via a behavior change). |

`tests/unit/test_utils.py` (unit) may hold any direct `serialize_hooks_by_type` checks (unregistered hook raises `UnserializableHookError` with the exact message; registered hook serializes unchanged) if the implementation stage finds them useful; the five tests above are the required set. Additionally, and not optionally, `tests/unit/test_utils.py`'s existing `test_serialize_hooks_by_type_uses_enum_value_and_name` and `test_serialize_hooks_by_type_name_fallback` must be updated per item 3 above so the full suite stays green — the first by registering its hook object first, the second by asserting the new `UnserializableHookError` instead of a successful dict.

## Constraints

No new dependencies, no public API removed, version stays 0.6.8. `ruff format` only files this subtask touches. Branch prefix `m1`, base `main`. Commit message: `fix(hooks): closures attach freely; saving one raises UnserializableHookError`. Verification: `uv run pytest tests/`; `uv run ruff format .` then `uv run ruff check .` (format restricted to touched files per the constraint above); no typecheck step.

## Out of scope

L1/L2 early-exit work (`agent.py`, `turn.py`); L4 `unregister(name)`; L5 `AFTER_TURN` ordering; `Agent.close()`; owner-qualified hook names or making closures saveable; re-queuing interrupted turns; the 0.7.0 bump/release and docs (Task 5.1); agent-manager follow-ups.

---

# Instance Hooks Attach Freely; Unsaveable Ones Refuse to Save — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let two closures with the same name be attached as instance hooks without a `ValueError`, and make saving an owner that holds an unregistered (closure or duplicate) hook raise a new `UnserializableHookError`.

**Architecture:** The saving step (`serialize_hooks_by_type` in `pygents/utils.py`) becomes the only place that decides whether a hook can be saved: a hook is saveable only if it is the exact object that `HookRegistry._registry` holds under its name. `HookRegistry.wrap` in `pygents/registry.py` stops raising on a name clash: it registers the new `Hook` only when the name is free, and otherwise returns it unregistered. The global `@hook(...)` path (`register_global`) is not touched.

**Tech Stack:** Python >= 3.12, pytest (no pytest-asyncio), ruff, uv.

**Spec:** `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-instance-hooks-attach-b0d9190f/docs/superpowers/specs/task-instance-hooks-attach-b0d9190f-design.md` (reproduced verbatim above). Parent documents: `docs/superpowers/specs/2026-09-25-lifecycle-fixes-design.md` (decision L3) and `docs/superpowers/plans/2026-09-25-lifecycle-fixes.md` (Task 2.1).

All paths below are relative to the worktree root `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-instance-hooks-attach-b0d9190f`, on branch `m1/task-instance-hooks-attach-b0d9190f`. Run every command from that directory. Do not assume any other subtask's code exists on this branch.

## Global Constraints

- No new dependencies. No public API removed. Version stays `0.6.8` (do not touch `pyproject.toml` or any version file).
- Test style: every test is a plain `def test_...()`. Async bodies run through `asyncio.run(...)`. No `async def test_...` and no pytest-asyncio.
- `tests/conftest.py` only clears `HookRegistry._global_hooks`. Every new test clears `HookRegistry` and `AgentRegistry` itself at the start (existing pattern in `tests/unit/test_hooks.py`).
- All new tests are unit tier: `tests/unit/test_hooks.py` and `tests/unit/test_utils.py`. Nothing goes in `tests/integration/`.
- Formatting: run `uv run ruff format` ONLY on the files this subtask changes (16 files on main are already unformatted and are out of scope). Do not run `uv run ruff format .` over the whole tree.
- Verification commands: `uv run pytest tests/`, `uv run ruff format <touched files>`, `uv run ruff check .`. No typecheck step. If `uv run pytest` reports pytest is not installed, use `uv run --extra dev pytest ...` / `uv run --extra dev ruff ...` instead (pytest and ruff live in the `dev` extra).
- Branch prefix `m1`, base `main`. The subtask lands as ONE commit with the exact message `fix(hooks): closures attach freely; saving one raises UnserializableHookError` (made at the end of Task 2; Task 1 does not commit).
- Out of scope: `pygents/agent.py`, `pygents/turn.py`, `pygents/context.py`, `BaseRegistry.register`, `HookRegistry.register_global`, `hook(...)` in `pygents/hooks.py`, the `hasattr(fn, "metadata")` branch of `wrap`, any `unregister` method, and any test file other than `tests/unit/test_hooks.py` and `tests/unit/test_utils.py`. If a pre-existing test in another file fails because it serializes an unregistered hook, stop and report it rather than editing that file.

## Review Focus

1. **Code that already catches `ValueError` around saving.** `UnserializableHookError` must be caught by `except ValueError`, so existing callers keep working. Test: `test_an_unsaveable_owner_is_caught_by_except_value_error` in Task 2.
2. **An instance closure whose name matches an existing global `@hook`.** Attaching it must not raise, must not replace or duplicate the global hook, and saving its owner must raise `UnserializableHookError`. Test: `test_an_instance_closure_named_like_a_global_hook_attaches_and_refuses_to_save` in Task 2.
3. **Owners other than `Agent.hooks` holding a closure** (a `Turn`'s instance hooks, an agent's `turn_hooks`). They share the serializer, so they must also refuse to save the second closure while the first still saves. Tests: `test_two_turns_from_one_factory_the_second_refuses_to_save` and `test_an_agent_turn_hook_closure_refuses_to_save` in Task 2.
4. **The first factory agent after the second has been built.** The registry entry must still point at the first agent's hook, so the first agent still round-trips through `to_dict()` / `from_dict()` and the restored agent holds the first closure, not the second. Test: `test_the_registry_keeps_the_first_factory_hook` in Task 2.
5. **A hook with no usable `__name__`, or a different object registered under the hook's name.** Saving must raise with the exact agreed message, not silently write a name that restores to the wrong hook. Tests: `test_serialize_hooks_by_type_name_fallback` (updated) and `test_serialize_hooks_by_type_raises_when_a_different_hook_holds_the_name` in Task 1.

---

### Task 1: `UnserializableHookError` and the saving check in `serialize_hooks_by_type`

**Files:**
- Modify: `pygents/errors.py` (append after line 28)
- Modify: `pygents/__init__.py:3-10` (import block) and `pygents/__init__.py:25-54` (`__all__`)
- Modify: `pygents/utils.py:6` (import) and `pygents/utils.py:203-217` (`serialize_hooks_by_type`)
- Test: `tests/unit/test_utils.py` (docstring lines 20-24, imports lines 27-41, tests at lines 206-223, new tests after line 223)

**Interfaces:**
- Consumes: `HookRegistry._registry` (dict keyed by `__name__`), `HookRegistry.register`, `HookRegistry.wrap`, `HookRegistry.clear` from `pygents/registry.py` (unchanged in this task).
- Produces: `class UnserializableHookError(ValueError)` in `pygents.errors`, exported as `pygents.UnserializableHookError`. `serialize_hooks_by_type(hooks: Iterable[Any]) -> dict[str, list[str]]` now raises `UnserializableHookError(f"hook {name!r} is not the one registered under that name (a closure or a duplicate); define it at module level to save its owner")` when `HookRegistry._registry.get(name) is not h`, for any hook whose `type` is not `None`.

- [ ] **Step 1: Update the decision table and imports in `tests/unit/test_utils.py`**

Replace lines 20-24 (the `serialize_hooks_by_type` block of the module docstring) with:

```python
serialize_hooks_by_type(hooks):
  HT1  hook has no hook_type or None -> skipped (before the registry check)
  HT2  hook_type has .value (enum) -> key = hook_type.value
  HT3  hook_type no .value -> key = str(hook_type)
  HT4  name = getattr(h, "__name__", "hook"); by_type[key].append(name)
  HT5  HookRegistry._registry.get(name) is not h -> UnserializableHookError
       (nothing registered under the name, or a closure/duplicate is)
```

Replace the import block at lines 27-41 with:

```python
import asyncio
import logging
import re

import pytest

import pygents
from pygents.errors import SafeExecutionError, UnserializableHookError
from pygents.hooks import TurnHook
from pygents.registry import HookRegistry
from pygents.utils import (
    eval_args,
    eval_kwargs,
    merge_kwargs,
    rebuild_hooks_from_serialization,
    safe_execution,
    serialize_hooks_by_type,
)
```

(The two existing `from pygents.registry import HookRegistry` lines inside `test_rebuild_hooks_from_serialization_*` stay as they are; they are harmless.)

- [ ] **Step 2: Update the two pre-existing serializer tests and add the new ones**

Replace lines 206-223 of `tests/unit/test_utils.py` (from `def test_serialize_hooks_by_type_uses_enum_value_and_name():` through `assert result == {"ev": ["hook"]}`), leaving `test_serialize_hooks_by_type_skips_hook_without_type` as it is, with:

```python
def test_serialize_hooks_by_type_uses_enum_value_and_name():
    HookRegistry.clear()
    hook1 = type("H", (), {"type": TurnHook.BEFORE_RUN, "__name__": "my_hook"})()
    HookRegistry.register(hook1)
    try:
        result = serialize_hooks_by_type([hook1])
        assert result == {"before_run": ["my_hook"]}
    finally:
        HookRegistry.clear()


def test_serialize_hooks_by_type_skips_hook_without_type():
    hook_no_type = type("H", (), {"__name__": "anonymous"})()
    assert serialize_hooks_by_type([hook_no_type]) == {}


def test_serialize_hooks_by_type_name_fallback():
    HookRegistry.clear()

    class E:
        value = "ev"

    hook_no_name = type("H", (), {"type": E()})()
    with pytest.raises(UnserializableHookError, match="'hook'"):
        serialize_hooks_by_type([hook_no_name])


def test_serialize_hooks_by_type_raises_for_unregistered_hook_with_exact_message():
    HookRegistry.clear()
    unregistered = type(
        "H", (), {"type": TurnHook.BEFORE_RUN, "__name__": "stray_hook"}
    )()
    expected = (
        "hook 'stray_hook' is not the one registered under that name "
        "(a closure or a duplicate); define it at module level to save its owner"
    )
    with pytest.raises(UnserializableHookError, match=f"^{re.escape(expected)}$"):
        serialize_hooks_by_type([unregistered])


def test_serialize_hooks_by_type_raises_when_a_different_hook_holds_the_name():
    HookRegistry.clear()

    async def same_name_hook(turn):
        pass

    registered = HookRegistry.wrap(same_name_hook, TurnHook.BEFORE_RUN)
    impostor = type(
        "H", (), {"type": TurnHook.BEFORE_RUN, "__name__": "same_name_hook"}
    )()
    assert serialize_hooks_by_type([registered]) == {
        "before_run": ["same_name_hook"]
    }
    with pytest.raises(UnserializableHookError, match="'same_name_hook'"):
        serialize_hooks_by_type([registered, impostor])


def test_unserializable_hook_error_is_a_value_error_exported_from_pygents():
    assert issubclass(UnserializableHookError, ValueError)
    assert pygents.UnserializableHookError is UnserializableHookError
    assert "UnserializableHookError" in pygents.__all__
```

- [ ] **Step 3: Run the serializer tests to verify they fail**

Run: `uv run pytest tests/unit/test_utils.py -v`
Expected: collection ERROR for the whole module with `ImportError: cannot import name 'UnserializableHookError' from 'pygents.errors'`.

- [ ] **Step 4: Add the error class**

Append to `pygents/errors.py` (after the `UnregisteredHookError` class, line 28):

```python


class UnserializableHookError(ValueError):
    """Raised when saving an owner that holds a hook which is not the one
    registered under its name (a closure or a duplicate)."""
```

- [ ] **Step 5: Export it from `pygents/__init__.py`**

Replace lines 3-10 of `pygents/__init__.py` with:

```python
from pygents.errors import (
    SafeExecutionError,
    TurnTimeoutError,
    UnregisteredAgentError,
    UnregisteredHookError,
    UnregisteredToolError,
    UnserializableHookError,
    WrongRunMethodError,
)
```

In `__all__`, replace

```python
    "UnregisteredToolError",
    "WrongRunMethodError",
```

with

```python
    "UnregisteredToolError",
    "UnserializableHookError",
    "WrongRunMethodError",
```

- [ ] **Step 6: Run the serializer tests to see the behavioral failures**

Run: `uv run pytest tests/unit/test_utils.py -v -k "serialize_hooks_by_type or unserializable"`
Expected: `test_serialize_hooks_by_type_name_fallback`, `test_serialize_hooks_by_type_raises_for_unregistered_hook_with_exact_message` and `test_serialize_hooks_by_type_raises_when_a_different_hook_holds_the_name` FAIL with `Failed: DID NOT RAISE <class 'pygents.errors.UnserializableHookError'>`. `test_serialize_hooks_by_type_empty`, `..._uses_enum_value_and_name`, `..._skips_hook_without_type` and `test_unserializable_hook_error_is_a_value_error_exported_from_pygents` PASS.

- [ ] **Step 7: Implement the check in `serialize_hooks_by_type`**

In `pygents/utils.py`, replace line 6:

```python
from pygents.errors import SafeExecutionError
```

with:

```python
from pygents.errors import SafeExecutionError, UnserializableHookError
```

Replace lines 203-217 (the whole `serialize_hooks_by_type` function) with:

```python
def serialize_hooks_by_type(hooks: Iterable[Any]) -> dict[str, list[str]]:
    """Serialize hooks by type.

    A hook can be saved only if it is the object registered in ``HookRegistry``
    under its name; otherwise restoring by name would give back a different hook.
    Hooks with no ``type`` are skipped.

    Raises
    ------
    UnserializableHookError
        If a hook is not the one registered under its name (a closure or a
        duplicate, or nothing is registered under that name).
    """
    hooks_dict: dict[str, list[str]] = {}
    for h in hooks:
        t = getattr(h, "type", None)
        if t is None:
            continue
        hook_name = getattr(h, "__name__", "hook")
        if HookRegistry._registry.get(hook_name) is not h:
            raise UnserializableHookError(
                f"hook {hook_name!r} is not the one registered under that name "
                "(a closure or a duplicate); define it at module level to save its owner"
            )
        types_to_add = t if isinstance(t, (tuple, frozenset)) else (t,)
        for single_type in types_to_add:
            key = (
                single_type.value if hasattr(single_type, "value") else str(single_type)
            )
            hooks_dict.setdefault(key, []).append(hook_name)
    return hooks_dict
```

- [ ] **Step 8: Run the serializer tests to verify they pass**

Run: `uv run pytest tests/unit/test_utils.py -v`
Expected: all tests in `tests/unit/test_utils.py` PASS.

- [ ] **Step 9: Run the full suite to confirm the tightened check breaks nothing else**

Run: `uv run pytest tests/`
Expected: all PASS. Every pre-existing hook that gets serialized in the suite is either an `@hook`-decorated global (registered) or attached through an instance decorator (registered by `wrap` when its name is free). If any test outside `tests/unit/test_utils.py` fails with `UnserializableHookError`, stop and report it; do not edit files outside this subtask's scope. Do not commit yet: the subtask lands as one commit at the end of Task 2.

---

### Task 2: `HookRegistry.wrap` no longer raises on a name clash

**Files:**
- Modify: `pygents/registry.py:131-169` (`HookRegistry.wrap`)
- Test: `tests/unit/test_hooks.py` (import inserted after line 26; new module-level hook and tests appended at the end of the file, after `test_cp_decorator_reuses_existing_hook`)

**Interfaces:**
- Consumes: `UnserializableHookError` from `pygents.errors` and the saving check in `serialize_hooks_by_type` (Task 1). `Agent(name, description, tools)`, `Agent.after_turn`, `Agent.on_complete`, `Agent.to_dict()`, `Agent.from_dict(data)`, `Agent.put(turn)`, `Agent.run()`, `Turn("tool_for_hook_test", kwargs={...})`, `Turn.before_run`, `Turn.to_dict()`, `hook(AgentHook.AFTER_TURN)`, and the module-level tool `tool_for_hook_test` already defined at `tests/unit/test_hooks.py:45-47`.
- Produces: `HookRegistry.wrap(fn, hook_type, *, lock=False, **fixed_kwargs) -> Hook` that never raises for a name clash on a plain function: returns the registered `Hook` if it already wraps `fn`; registers and returns a new `Hook` if the name is free; otherwise returns a new, unregistered `Hook`.

- [ ] **Step 1: Add the import in `tests/unit/test_hooks.py`**

After line 26 (`from pygents.context import ContextItem, ContextPool, ContextQueue`), insert:

```python
from pygents.errors import UnserializableHookError
```

so lines 25-39 read:

```python
from pygents.agent import Agent
from pygents.context import ContextItem, ContextPool, ContextQueue
from pygents.errors import UnserializableHookError
from pygents.hooks import (
    AgentHook,
    ContextPoolHook,
    ContextQueueHook,
    HookMetadata,
    ToolHook,
    TurnHook,
    hook,
)
from pygents.registry import AgentRegistry, HookRegistry
from pygents.tool import tool
from pygents.turn import StopReason, Turn
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/unit/test_hooks.py` (after `test_cp_decorator_reuses_existing_hook`, line 1363):

```python


# ---------------------------------------------------------------------------
# Closure hooks: instance hooks attach freely; unsaveable ones refuse to save
# ---------------------------------------------------------------------------


async def module_level_after_turn_hook(agent, turn):
    pass


def _make_factory_agent(name, fired=None):
    agent = Agent(name, "d", [tool_for_hook_test])

    @agent.after_turn
    async def log_turn(agent, turn):
        if fired is not None:
            fired.append((name, agent.name))

    return agent


def test_two_agents_from_one_factory_both_construct_and_the_second_refuses_to_save():
    HookRegistry.clear()
    AgentRegistry.clear()

    def make(name):
        agent = Agent(name, "d", [tool_for_hook_test])

        @agent.after_turn
        async def log_turn(agent, turn):
            pass

        return agent

    one, two = make("one"), make("two")  # no ValueError
    one.to_dict()  # the registered hook: saves
    with pytest.raises(UnserializableHookError, match="log_turn"):
        two.to_dict()


def test_both_factory_agents_fire_their_own_hook():
    HookRegistry.clear()
    AgentRegistry.clear()
    fired = []
    one = _make_factory_agent("one", fired)
    two = _make_factory_agent("two", fired)

    async def run(agent):
        await agent.put(Turn("tool_for_hook_test", kwargs={"x": 1}))
        async for _ in agent.run():
            pass

    asyncio.run(run(one))
    assert fired == [("one", "one")]
    asyncio.run(run(two))
    assert fired == [("one", "one"), ("two", "two")]


def test_a_module_level_instance_hook_still_round_trips():
    HookRegistry.clear()
    AgentRegistry.clear()
    agent = Agent("module_hook_agent", "d", [tool_for_hook_test])
    attached = agent.after_turn(module_level_after_turn_hook)
    assert HookRegistry.get("module_level_after_turn_hook") is attached

    data = agent.to_dict()
    assert data["hooks"] == {"after_turn": ["module_level_after_turn_hook"]}

    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert len(restored.hooks) == 1
    assert restored.hooks[0] is attached


def test_one_module_level_hook_on_two_agents_saves_twice():
    HookRegistry.clear()
    AgentRegistry.clear()
    first = Agent("first_sharer", "d", [tool_for_hook_test])
    second = Agent("second_sharer", "d", [tool_for_hook_test])
    on_first = first.after_turn(module_level_after_turn_hook)
    on_second = second.after_turn(module_level_after_turn_hook)
    assert on_first is on_second

    expected = {"after_turn": ["module_level_after_turn_hook"]}
    assert first.to_dict()["hooks"] == expected
    assert second.to_dict()["hooks"] == expected


def test_two_global_hooks_with_one_name_still_raise():
    HookRegistry.clear()

    def make_global():
        @hook(AgentHook.AFTER_TURN)
        async def shared_global_name(agent, turn):
            pass

        return shared_global_name

    first = make_global()
    with pytest.raises(ValueError, match=r"'shared_global_name' already registered") as exc_info:
        make_global()
    assert type(exc_info.value) is ValueError
    assert HookRegistry.get("shared_global_name") is first
    assert HookRegistry._global_hooks == [first]


def test_an_unsaveable_owner_is_caught_by_except_value_error():
    HookRegistry.clear()
    AgentRegistry.clear()
    _make_factory_agent("one")
    two = _make_factory_agent("two")
    with pytest.raises(ValueError, match="log_turn"):
        two.to_dict()


def test_the_registry_keeps_the_first_factory_hook():
    HookRegistry.clear()
    AgentRegistry.clear()
    fired = []
    one = _make_factory_agent("one", fired)
    two = _make_factory_agent("two", fired)
    assert HookRegistry.get("log_turn") is one.hooks[0]
    assert two.hooks[0] is not one.hooks[0]

    data = one.to_dict()
    AgentRegistry.clear()
    restored = Agent.from_dict(data)
    assert restored.hooks == [one.hooks[0]]

    async def run():
        await restored.put(Turn("tool_for_hook_test", kwargs={"x": 1}))
        async for _ in restored.run():
            pass

    asyncio.run(run())
    assert fired == [("one", "one")]


def test_an_instance_closure_named_like_a_global_hook_attaches_and_refuses_to_save():
    HookRegistry.clear()
    AgentRegistry.clear()

    @hook(AgentHook.AFTER_TURN)
    async def audit_turn_global(agent, turn):
        pass

    async def local_audit(agent, turn):
        pass

    local_audit.__name__ = "audit_turn_global"

    agent = Agent("shadowing_agent", "d", [tool_for_hook_test])
    attached = agent.after_turn(local_audit)  # no ValueError
    assert attached is not audit_turn_global
    assert attached.fn is local_audit
    assert HookRegistry.get("audit_turn_global") is audit_turn_global
    assert HookRegistry._global_hooks == [audit_turn_global]
    with pytest.raises(UnserializableHookError, match="audit_turn_global"):
        agent.to_dict()


def test_two_turns_from_one_factory_the_second_refuses_to_save():
    HookRegistry.clear()

    def make_turn():
        turn = Turn("tool_for_hook_test", kwargs={"x": 1})

        @turn.before_run
        async def note_start(turn):
            pass

        return turn

    first, second = make_turn(), make_turn()  # no ValueError
    assert first.to_dict()["hooks"] == {"before_run": ["note_start"]}
    with pytest.raises(UnserializableHookError, match="note_start"):
        second.to_dict()


def test_an_agent_turn_hook_closure_refuses_to_save():
    HookRegistry.clear()
    AgentRegistry.clear()

    def make(name):
        agent = Agent(name, "d", [tool_for_hook_test])

        @agent.on_complete
        async def log_complete(turn, stop_reason):
            pass

        return agent

    one, two = make("tc_one"), make("tc_two")  # no ValueError
    assert one.to_dict()["turn_hooks"] == {"on_complete": ["log_complete"]}
    with pytest.raises(UnserializableHookError, match="log_complete"):
        two.to_dict()
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/test_hooks.py -v -k "factory or module_level or global_hooks_with_one_name or except_value_error or first_factory_hook or named_like_a_global or two_turns_from_one_factory or turn_hook_closure"`
Expected:
- FAIL with `ValueError: 'log_turn' already registered` (raised from `BaseRegistry.register` via `HookRegistry.wrap`, while building the second agent): `test_two_agents_from_one_factory_both_construct_and_the_second_refuses_to_save`, `test_both_factory_agents_fire_their_own_hook`, `test_the_registry_keeps_the_first_factory_hook`, `test_an_unsaveable_owner_is_caught_by_except_value_error` (its second agent is built outside the `pytest.raises` block).
- FAIL with `ValueError: 'audit_turn_global' already registered`: `test_an_instance_closure_named_like_a_global_hook_attaches_and_refuses_to_save`.
- FAIL with `ValueError: 'note_start' already registered`: `test_two_turns_from_one_factory_the_second_refuses_to_save`.
- FAIL with `ValueError: 'log_complete' already registered`: `test_an_agent_turn_hook_closure_refuses_to_save`.
- PASS already (regression guards for behavior this subtask must keep): `test_a_module_level_instance_hook_still_round_trips`, `test_one_module_level_hook_on_two_agents_saves_twice`, `test_two_global_hooks_with_one_name_still_raise`.

- [ ] **Step 4: Implement the new `wrap`**

In `pygents/registry.py`, replace lines 131-169 (the whole `wrap` classmethod, from `@classmethod` above `def wrap(` through `return wrapper`) with:

```python
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
```

(`UnregisteredHookError` stays imported at the top of `pygents/registry.py`: it is still used as `BaseRegistry._not_found_error` on line 24.)

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/unit/test_hooks.py -v -k "factory or module_level or global_hooks_with_one_name or except_value_error or first_factory_hook or named_like_a_global or two_turns_from_one_factory or turn_hook_closure"`
Expected: all 10 selected tests PASS.

- [ ] **Step 6: Run the hook, utils and registry test files**

Run: `uv run pytest tests/unit/test_hooks.py tests/unit/test_utils.py tests/unit/test_registry.py -v`
Expected: all PASS (in particular `test_hook_registry_register_duplicate_raises_value_error` in `tests/unit/test_registry.py` still passes, since `BaseRegistry.register` is unchanged).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest tests/`
Expected: all PASS.

- [ ] **Step 8: Format only the touched files and lint**

Run: `uv run ruff format pygents/errors.py pygents/__init__.py pygents/registry.py pygents/utils.py tests/unit/test_hooks.py tests/unit/test_utils.py`
Expected: files reformatted or left unchanged; no errors.

Run: `uv run ruff check .`
Expected: `All checks passed!`

Run: `uv run pytest tests/`
Expected: all PASS after formatting.

- [ ] **Step 9: Commit**

```bash
git add pygents/errors.py pygents/__init__.py pygents/registry.py pygents/utils.py tests/unit/test_hooks.py tests/unit/test_utils.py
git commit -m "fix(hooks): closures attach freely; saving one raises UnserializableHookError"
```

Confirm with `git status` that only those six files were in the commit and that `pyproject.toml` (version `0.6.8`) is untouched.
