<!-- task-pipeline: validated -->
# Subtask 82dc9dcd: Unregister one registry entry by name

Parent story: 05826899 "Registry removal" (milestone 909eb7ad). This is a narrowed version of plan Task 3.1 in `docs/superpowers/plans/2026-09-25-lifecycle-fixes.md` (Story 3). Branch prefix `m1`, base `main`.

## Scope

- Modify: `pygents/registry.py`. Add `unregister` to `BaseRegistry` and override it on `HookRegistry`.
- Tests: `tests/unit/test_registry.py`. Also extend the module's decision-table docstring to cover the new behaviors (a repo convention).
- No new error types, no new dependencies, no public API removed, and no version change (stays 0.6.8).

## Out of scope (owned elsewhere, do not touch)

- `pygents/turn.py` cancellation and `Agent.run()` early-exit cleanup (Story 1, Tasks 1.1/1.2).
- `HookRegistry.wrap`, `serialize_hooks_by_type`, `UnserializableHookError`, `pygents/__init__.py`, owner-qualified hook names, and saving closures (Story 2, Task 2.1).
- Moving `self._current_turn = None` in `pygents/agent.py` (Story 4, Task 4.1).
- Docs and `mkdocs.yml` (Story 5, Task 5.1). `Agent.close()` and the context-manager API. Re-queuing interrupted turns. The version bump and release.

## Observable behavior

- `BaseRegistry.unregister(cls, name: str) -> None` (classmethod) removes `name` from `cls._registry`. After that, `get(name)` raises the registry's not-found error, and a new item with the same name can be registered without `ValueError`.
- If `name` is not in `_registry`, it raises `cls._not_found_error(f"{name!r} not found")`, following the same pattern as `get()` (`registry.py:40-45`). The error type depends on the registry:
  - `ToolRegistry` raises `UnregisteredToolError`.
  - `AgentRegistry` raises `UnregisteredAgentError`.
  - `HookRegistry` raises `UnregisteredHookError` (inherited default).
  - All three are `KeyError` subclasses (`pygents/errors.py:19-27`).
- `HookRegistry.unregister(name)` also removes that same hook object (matched by identity) from `_global_hooks`, so it stops firing globally. For unknown names it raises the same error as the base method, and `_global_hooks` is left unchanged. A hook registered only by name (via `wrap`) is not in `_global_hooks`, and unregistering it just removes the name. Instance hook lists (`obj.hooks`) are not touched.
- Unregistering only affects lookup by name. Objects that are already referenced elsewhere keep working. For example, an agent holding a tool object keeps running turns with it, but `Turn("<name>")` or `ToolRegistry.get("<name>")` now raises `UnregisteredToolError` (Review Focus 5).

## Decision-table additions (docstring of `tests/unit/test_registry.py`)

- TR6: `unregister(name)` when name is in `_registry`: delete it, then `get` raises and re-registering succeeds. TR7: `unregister(name)` when name is not in `_registry`: `UnregisteredToolError`.
- AR6/AR7: the same two rows for `AgentRegistry` with `UnregisteredAgentError`.
- HR9: `unregister(name)` when name is in `_registry`: delete it and remove that hook object from `_global_hooks`. HR10: `unregister(name)` when name is not in `_registry`: `UnregisteredHookError`.

## Tests

Tier rule: the plan's Global Constraints say "Tests live in tests/unit/ next to the module's existing tests". Repo convention also puts single-module, isolated behavior in `tests/unit/` and multi-component flows in `tests/integration/`. This subtask changes only `pygents/registry.py`, so every test below goes in **unit** (`tests/unit/test_registry.py`).

Conventions:
- No pytest-asyncio. Async bodies are written as `def test_...(): asyncio.run(_body())`, and the existing `collect_async` helper in `tests/conftest.py` can be used where needed.
- The conftest autouse fixture only resets `HookRegistry._global_hooks`. Each test must call `AgentRegistry.clear()`, `ToolRegistry.clear()` and `HookRegistry.clear()` itself for the registries it uses.

1. `test_unregister_frees_the_name` (unit). Parametrized over `(AgentRegistry, make_agent_named, UnregisteredAgentError)`, `(ToolRegistry, make_tool_named, UnregisteredToolError)` and `(HookRegistry, make_hook_named, UnregisteredHookError)`. Each case:
   - creates or registers `"x"`, then calls `unregister("x")`;
   - asserts `get("x")` raises the error for that registry;
   - creates `"x"` again with no `ValueError`;
   - asserts `unregister("nope")` raises that registry's error.
   Covers TR6/TR7, AR6/AR7 and HR9/HR10 (name part).
2. `test_an_unregistered_global_hook_no_longer_fires` (unit, sync wrapper around `asyncio.run`).
   - Setup: register a global hook with `@hook(SomeType)`, confirm it is in `_global_hooks`, then `HookRegistry.unregister(<name>)`.
   - Asserts: it is gone from `_global_hooks` and from `get_global_by_type`, and `HookRegistry.fire(SomeType, [], ...)` (or a real dispatch) does not call it. Covers HR9 (global part).
3. `test_unregistering_a_tool_leaves_live_agents_working` (unit, sync wrapper around `asyncio.run`). Review Focus 5.
   - Setup: build an agent that holds a registered tool, construct a `Turn` for that tool and `await agent.put(turn)` while the tool is still registered, and only then call `ToolRegistry.unregister(tool.__name__)`. This order is required: `Turn.__init__` always calls `ToolRegistry.get(...)` to resolve `self.tool` — even when passed the tool object rather than its name (`pygents/turn.py:118-121`) — so a `Turn` cannot be constructed for that tool after it is unregistered.
   - Asserts: `agent.run()` still runs the already-queued turn (built and queued before unregister) and gets the expected result, while `Turn(tool.__name__)` / `ToolRegistry.get(tool.__name__)` raises `UnregisteredToolError` if attempted afterward.

## Verification

Commands given by the harness:
- Full suite: `uv run pytest tests/`
- Lint: `uv run ruff format .` and `uv run ruff check .`
- No typecheck command.

The plan's own commands differ. It uses `uv run --extra dev pytest tests/` and `uv run --extra dev ruff check .`, because pytest and ruff live in the dev extra. It also says to run `ruff format` only on the files you touched (`pygents/registry.py`, `tests/unit/test_registry.py`), since 16 files on main are already unformatted and are out of scope.

Commit: `feat(registry): unregister one entry by name`.

---

# Unregister One Registry Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a classmethod `unregister(name)` to every pygents registry. It frees a name for lookup and reuse, and on `HookRegistry` it also stops that hook from firing globally.

**Architecture:** `BaseRegistry.unregister` mirrors `BaseRegistry.get`: the same not-found error, then `del cls._registry[name]`. `HookRegistry.unregister` first resolves the hook with `cls.get(name)`, so an unknown name raises before anything changes. It then calls the base method and filters that exact object (`is not`) out of `_global_hooks`. It never touches instance `obj.hooks` lists or objects that already hold a reference.

**Tech Stack:** Python >=3.12, pytest (no pytest-asyncio; async bodies run via `asyncio.run`), ruff, uv.

**Spec:** `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd/docs/superpowers/specs/task-unregister-one-registry-82dc9dcd-design.md` (reproduced verbatim above).

**Worktree / branch:** `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd`, branch `m1/task-unregister-one-registry-82dc9dcd`. It was cut from `m1/task-instance-hooks-attach-b0d9190f`. Do not assume any other subtask's code exists here beyond what is described below as already present. Every command below starts with `cd` into the worktree because shell cwd is not preserved between calls.

## Global Constraints

- Modify only `pygents/registry.py` and `tests/unit/test_registry.py`.
- No new error types, no new dependencies, no public API removed, and no version change (stays 0.6.8).
- Not-found message format: `f"{name!r} not found"`, raised as `cls._not_found_error(...)`. That gives `UnregisteredToolError` for `ToolRegistry`, `UnregisteredAgentError` for `AgentRegistry` and `UnregisteredHookError` for `HookRegistry`.
- `HookRegistry.unregister` removes the hook from `_global_hooks` by identity. A failed call leaves `_global_hooks` unchanged. Instance `obj.hooks` lists are never touched.
- Tests go in the unit tier, `tests/unit/test_registry.py`. There is no pytest-asyncio, so async bodies use `asyncio.run(...)` inside a plain `def test_...`.
- Registry hygiene: the conftest autouse fixture only resets `HookRegistry._global_hooks`, so tests call `AgentRegistry.clear()` and `HookRegistry.clear()` themselves. **Deviation from the spec's wording, on purpose:** do NOT call a bare `ToolRegistry.clear()`. Module-level `@tool()` functions in `tests/unit/test_agent.py`, `test_hooks.py`, `test_turn.py` and this file are registered once, at import time. A bare clear would break every test module that runs later. `tests/unit/test_turn.py:705-715` (`isolated_tool_registry`) documents the same hazard. Instead, the tests that touch `ToolRegistry` use a snapshot/restore fixture, `restore_tool_registry`.
- Out of scope, do not touch: `pygents/turn.py`, `pygents/agent.py`, `HookRegistry.wrap`, `pygents/utils.py`, `pygents/errors.py`, `pygents/__init__.py`, docs, `mkdocs.yml`, the version.
- Verification commands (harness, verbatim): `uv run pytest tests/`, `uv run ruff format .`, `uv run ruff check .`. If `uv run pytest` cannot find pytest, use `uv run --extra dev pytest ...` (pytest and ruff live in the `dev` extra).

## Review Focus

1. A failed `HookRegistry.unregister("unknown")` must leave `_global_hooks` and the other registered names exactly as they were. Nothing half-removed. Pinned by `test_unregister_unknown_hook_leaves_global_hooks_unchanged` (Task 2).
2. A hook that is both global and in an instance list (for example `turn.hooks`) must keep firing from that instance list after it is unregistered. Only the global dispatch stops. Pinned by `test_unregister_does_not_touch_instance_hook_lists` (Task 2).
3. Removal from `_global_hooks` is by identity. Unregistering one global hook must not remove other global hooks of the same type. Unregistering a hook that is only registered by name must leave `_global_hooks` alone. Pinned by `test_unregister_removes_only_that_hook_from_global_hooks` (Task 2).
4. Unregistering the same name twice must raise the registry's not-found error on the second call, not pass silently. Pinned inside `test_unregister_frees_the_name` (Task 1).
5. An agent or queued turn that already holds a tool object keeps working after that tool's name is unregistered. Only lookup by name (`Turn("<name>")`, `ToolRegistry.get`) fails. Pinned by `test_unregistering_a_tool_leaves_live_agents_working` (Task 1).

---

## File Structure

- `pygents/registry.py`: add `BaseRegistry.unregister` after `BaseRegistry.get` (currently lines 40-45), and `HookRegistry.unregister` after `HookRegistry.clear` (currently lines 83-86). No other file in `pygents/` changes.
- `tests/unit/test_registry.py`: extend the decision-table docstring (lines 1-29), add imports (lines 31-40), and append the new fixture, helpers and tests at the end of the file (after line 311).

---

### Task 1: `BaseRegistry.unregister` frees a name in every registry

**Files:**
- Modify: `pygents/registry.py:40-45` (insert the new method directly after `get`)
- Test: `tests/unit/test_registry.py` (docstring lines 1-29, imports lines 31-40, append at end of file)

**Interfaces:**
- Consumes: the existing `BaseRegistry._registry`, `BaseRegistry._not_found_error`, `AgentRegistry`, `ToolRegistry`, `HookRegistry`, `Agent`, `Turn`, `tool`, and the module-level `_registry_test_tool` in `tests/unit/test_registry.py:86-88`.
- Produces: `BaseRegistry.unregister(cls, name: str) -> None` (classmethod), inherited by `ToolRegistry`, `AgentRegistry` and `HookRegistry`. It also produces the test fixture `restore_tool_registry` and the helpers `_make_agent_named(name: str) -> Agent`, `_make_tool_named(name: str) -> Tool`, `_make_hook_named(name: str) -> Callable` in `tests/unit/test_registry.py`. Task 2 does not depend on these helpers.

- [ ] **Step 1: Extend the decision-table docstring for the tool and agent rows plus HR10**

In `tests/unit/test_registry.py`, replace:

```python
  TR5  all() -> list(_registry.values())
```

with:

```python
  TR5  all() -> list(_registry.values())
  TR6  unregister(name): in _registry -> del _registry[name]; get(name) then raises, re-register succeeds
  TR7  unregister(name): not in _registry -> UnregisteredToolError
```

Replace:

```python
  AR5  get(name): in _registry -> return agent
```

with:

```python
  AR5  get(name): in _registry -> return agent
  AR6  unregister(name): in _registry -> del _registry[name]; get(name) then raises, re-register succeeds
  AR7  unregister(name): not in _registry -> UnregisteredAgentError
```

Replace:

```python
  HR8  get_by_type(hook_type, hooks) -> list of all hooks in hooks matching hook_type, in order
```

with:

```python
  HR8  get_by_type(hook_type, hooks) -> list of all hooks in hooks matching hook_type, in order
  HR10 unregister(name): not in _registry -> UnregisteredHookError; _global_hooks unchanged
```

(HR9 is added in Task 2, together with the behavior it describes.)

- [ ] **Step 2: Add the imports the new tests need**

In `tests/unit/test_registry.py`, replace:

```python
import pytest

from pygents.agent import Agent
```

with:

```python
import asyncio

import pytest

from pygents.agent import Agent
```

And replace:

```python
from pygents.registry import AgentRegistry, HookRegistry, ToolRegistry
from pygents.tool import tool
```

with:

```python
from pygents.hooks import TurnHook, hook
from pygents.registry import AgentRegistry, HookRegistry, ToolRegistry
from pygents.tool import tool
from pygents.turn import Turn
```

(`TurnHook` and `hook` are first used in Task 2. `ruff check` flags unused imports (F401), so if you run ruff between tasks, add those two names in Task 2 instead. The final state is the same.)

- [ ] **Step 3: Write the failing tests (fixture, helpers, and two tests) at the end of `tests/unit/test_registry.py`**

Append:

```python


# ---------------------------------------------------------------------------
# unregister (TR6/TR7, AR6/AR7, HR10)
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_tool_registry():
    """Snapshot ToolRegistry and restore it after the test.

    Module-level tools across the suite are registered once at import time,
    so a bare ToolRegistry.clear() would break every test module that runs
    later (same hazard as isolated_tool_registry in tests/unit/test_turn.py).
    """
    saved = dict(ToolRegistry._registry)
    yield
    ToolRegistry._registry = saved


def _make_agent_named(name: str) -> Agent:
    return Agent(name, "For unregister tests", [_registry_test_tool])


def _make_tool_named(name: str):
    async def fn() -> None:
        return None

    fn.__name__ = name
    return tool(fn)


def _make_hook_named(name: str):
    async def fn(*args, **kwargs) -> None:
        return None

    fn.__name__ = name
    HookRegistry.register(fn)
    return fn


@pytest.mark.parametrize(
    ("registry", "make", "not_found"),
    [
        (AgentRegistry, _make_agent_named, UnregisteredAgentError),
        (ToolRegistry, _make_tool_named, UnregisteredToolError),
        (HookRegistry, _make_hook_named, UnregisteredHookError),
    ],
    ids=["agent", "tool", "hook"],
)
def test_unregister_frees_the_name(restore_tool_registry, registry, make, not_found):
    AgentRegistry.clear()
    HookRegistry.clear()

    first = make("unregister_me")
    assert registry.get("unregister_me") is first

    assert registry.unregister("unregister_me") is None

    with pytest.raises(not_found, match=r"'unregister_me' not found"):
        registry.get("unregister_me")

    # The name is free again: a *different* object can take it without ValueError.
    second = make("unregister_me")
    assert second is not first
    assert registry.get("unregister_me") is second

    # Unknown name -> the registry's own not-found error.
    with pytest.raises(not_found, match=r"'nope' not found"):
        registry.unregister("nope")

    # Unregistering twice: the second call is an unknown name (Review Focus 4).
    registry.unregister("unregister_me")
    with pytest.raises(not_found, match=r"'unregister_me' not found"):
        registry.unregister("unregister_me")


def test_unregistering_a_tool_leaves_live_agents_working(restore_tool_registry):
    AgentRegistry.clear()

    @tool()
    async def unregister_live_tool(x: int) -> int:
        return x * 2

    agent = Agent("unregister_live_agent", "Holds the tool", [unregister_live_tool])

    async def _body():
        # Turn.__init__ resolves the tool through ToolRegistry.get, so the turn
        # must be built and queued while the name is still registered.
        await agent.put(Turn("unregister_live_tool", kwargs={"x": 21}))
        ToolRegistry.unregister("unregister_live_tool")
        return [value async for _, value in agent.run()]

    assert asyncio.run(_body()) == [42]
    assert agent.tools == [unregister_live_tool]

    with pytest.raises(UnregisteredToolError, match=r"'unregister_live_tool' not found"):
        ToolRegistry.get("unregister_live_tool")
    with pytest.raises(UnregisteredToolError, match=r"'unregister_live_tool' not found"):
        Turn("unregister_live_tool")
```

- [ ] **Step 4: Run the new tests and confirm they fail**

Run:

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && uv run pytest tests/unit/test_registry.py -k "unregister_frees_the_name or leaves_live_agents_working" -v
```

Expected: 4 FAILED (`test_unregister_frees_the_name[agent]`, `[tool]`, `[hook]`, `test_unregistering_a_tool_leaves_live_agents_working`). Each fails with `AttributeError: type object '<AgentRegistry|ToolRegistry|HookRegistry>' has no attribute 'unregister'`. If anything fails for another reason (import error, fixture error), fix the test before moving on.

- [ ] **Step 5: Implement `BaseRegistry.unregister`**

In `pygents/registry.py`, replace:

```python
    @classmethod
    def get(cls, name: str) -> T:
        item = cls._registry.get(name)
        if item is None:
            raise cls._not_found_error(f"{name!r} not found")
        return item
```

with:

```python
    @classmethod
    def get(cls, name: str) -> T:
        item = cls._registry.get(name)
        if item is None:
            raise cls._not_found_error(f"{name!r} not found")
        return item

    @classmethod
    def unregister(cls, name: str) -> None:
        """Remove *name* so it can no longer be looked up and can be reused.

        Only lookup by name is affected: objects that already hold the item
        (e.g. an agent holding a tool) keep working.

        Raises ``cls._not_found_error`` if *name* is not registered.
        """
        if name not in cls._registry:
            raise cls._not_found_error(f"{name!r} not found")
        del cls._registry[name]
```

- [ ] **Step 6: Run the new tests and confirm they pass**

Run:

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && uv run pytest tests/unit/test_registry.py -k "unregister_frees_the_name or leaves_live_agents_working" -v
```

Expected: 4 passed.

- [ ] **Step 7: Run the whole registry test module to check for regressions**

Run:

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && uv run pytest tests/unit/test_registry.py -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && git add pygents/registry.py tests/unit/test_registry.py && git commit -m "feat(registry): unregister one entry by name"
```

---

### Task 2: `HookRegistry.unregister` also stops the hook firing globally

**Files:**
- Modify: `pygents/registry.py:83-86` (insert the override directly after `HookRegistry.clear`)
- Test: `tests/unit/test_registry.py` (docstring HR rows, append at end of file)

**Interfaces:**
- Consumes: `BaseRegistry.unregister(cls, name: str) -> None` from Task 1, plus the existing `HookRegistry.get`, `HookRegistry._global_hooks`, `HookRegistry.get_global_by_type`, `HookRegistry.fire`, `hook`, `TurnHook`, `Turn`, `_registry_test_tool`, and `Turn.returning()` (fires `TurnHook.BEFORE_RUN` on the turn's own `hooks` list, then global hooks, deduplicated by identity).
- Produces: `HookRegistry.unregister(cls, name: str) -> None` (classmethod override), with the same signature as the base method.

- [ ] **Step 1: Add HR9 to the decision-table docstring**

In `tests/unit/test_registry.py`, replace:

```python
  HR10 unregister(name): not in _registry -> UnregisteredHookError; _global_hooks unchanged
```

with:

```python
  HR9  unregister(name): in _registry -> del _registry[name]; remove that hook object (by identity) from _global_hooks; instance hook lists untouched
  HR10 unregister(name): not in _registry -> UnregisteredHookError; _global_hooks unchanged
```

(If you deferred the `TurnHook, hook` import in Task 1 Step 2, add `from pygents.hooks import TurnHook, hook` above `from pygents.registry import ...` now.)

- [ ] **Step 2: Write the failing tests at the end of `tests/unit/test_registry.py`**

Append:

```python


# ---------------------------------------------------------------------------
# HookRegistry.unregister and global hooks (HR9, HR10)
# ---------------------------------------------------------------------------


def test_an_unregistered_global_hook_no_longer_fires():
    HookRegistry.clear()
    calls = []

    @hook(TurnHook.BEFORE_RUN)
    async def unregister_global_hook(turn):
        calls.append(turn)

    assert unregister_global_hook in HookRegistry._global_hooks
    asyncio.run(HookRegistry.fire(TurnHook.BEFORE_RUN, [], "first"))
    assert calls == ["first"]

    HookRegistry.unregister("unregister_global_hook")

    assert unregister_global_hook not in HookRegistry._global_hooks
    assert HookRegistry.get_global_by_type(TurnHook.BEFORE_RUN) == []
    asyncio.run(HookRegistry.fire(TurnHook.BEFORE_RUN, [], "second"))
    assert calls == ["first"]


def test_unregister_removes_only_that_hook_from_global_hooks():
    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def removed_global_hook(turn):
        pass

    @hook(TurnHook.BEFORE_RUN)
    async def kept_global_hook(turn):
        pass

    async def name_only_hook(turn):
        pass

    HookRegistry.register(name_only_hook)

    HookRegistry.unregister("removed_global_hook")
    assert HookRegistry._global_hooks == [kept_global_hook]
    assert HookRegistry.get("kept_global_hook") is kept_global_hook

    # A hook known only by name is not in _global_hooks; unregistering it
    # just frees the name and leaves the global list alone.
    HookRegistry.unregister("name_only_hook")
    assert HookRegistry._global_hooks == [kept_global_hook]
    with pytest.raises(UnregisteredHookError, match=r"'name_only_hook' not found"):
        HookRegistry.get("name_only_hook")


def test_unregister_unknown_hook_leaves_global_hooks_unchanged():
    HookRegistry.clear()

    @hook(TurnHook.BEFORE_RUN)
    async def still_global_hook(turn):
        pass

    before = list(HookRegistry._global_hooks)
    with pytest.raises(UnregisteredHookError, match=r"'nope' not found"):
        HookRegistry.unregister("nope")

    assert HookRegistry._global_hooks == before
    assert HookRegistry.get("still_global_hook") is still_global_hook


def test_unregister_does_not_touch_instance_hook_lists():
    HookRegistry.clear()
    calls = []

    @hook(TurnHook.BEFORE_RUN)
    async def shared_hook(turn):
        calls.append(turn)

    turn = Turn("_registry_test_tool", kwargs={"x": 1})
    turn.hooks.append(shared_hook)

    HookRegistry.unregister("shared_hook")

    assert turn.hooks == [shared_hook]
    assert asyncio.run(turn.returning()) == 1
    # Fires exactly once: from the turn's own list, no longer from the global list.
    assert calls == [turn]
```

- [ ] **Step 3: Run the new tests and confirm the right ones fail**

Run:

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && uv run pytest tests/unit/test_registry.py -k "unregistered_global_hook or removes_only_that_hook or unknown_hook_leaves or does_not_touch_instance" -v
```

Expected:
- FAILED `test_an_unregistered_global_hook_no_longer_fires` with `AssertionError` on `assert unregister_global_hook not in HookRegistry._global_hooks`. The inherited base `unregister` frees the name but leaves the hook in `_global_hooks`.
- FAILED `test_unregister_removes_only_that_hook_from_global_hooks` with `AssertionError` on `assert HookRegistry._global_hooks == [kept_global_hook]` (the list still has `removed_global_hook` in it).
- PASSED `test_unregister_unknown_hook_leaves_global_hooks_unchanged` and `test_unregister_does_not_touch_instance_hook_lists`. These two are regression guards (Review Focus 1 and 2) for properties the override must preserve. They are expected to pass now and must still pass after Step 4.

- [ ] **Step 4: Implement the `HookRegistry.unregister` override**

In `pygents/registry.py`, replace:

```python
    @classmethod
    def clear(cls) -> None:
        super().clear()
        cls._global_hooks = []
```

with:

```python
    @classmethod
    def clear(cls) -> None:
        super().clear()
        cls._global_hooks = []

    @classmethod
    def unregister(cls, name: str) -> None:
        """Remove *name* and stop that same hook object from firing globally.

        Resolves the hook first, so an unknown name raises
        ``UnregisteredHookError`` without changing ``_global_hooks``.
        Instance hook lists (``obj.hooks``) are not touched.
        """
        hook = cls.get(name)
        super().unregister(name)
        cls._global_hooks = [h for h in cls._global_hooks if h is not hook]
```

- [ ] **Step 5: Run the new tests and confirm they pass**

Run:

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && uv run pytest tests/unit/test_registry.py -k "unregistered_global_hook or removes_only_that_hook or unknown_hook_leaves or does_not_touch_instance" -v
```

Expected: 4 passed.

- [ ] **Step 6: Run the whole registry test module**

Run:

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && uv run pytest tests/unit/test_registry.py -v
```

Expected: all tests pass, including Task 1's `test_unregister_frees_the_name[hook]`, which now goes through the override.

- [ ] **Step 7: Commit**

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && git add pygents/registry.py tests/unit/test_registry.py && git commit -m "feat(registry): unregistering a hook removes it from global hooks"
```

---

### Task 3: Full verification

**Files:**
- No new edits expected. Formatting fixes are allowed only in `pygents/registry.py` and `tests/unit/test_registry.py`.

**Interfaces:**
- Consumes: everything from Tasks 1 and 2.
- Produces: nothing new.

- [ ] **Step 1: Run the full suite**

Run:

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && uv run pytest tests/
```

Expected: all tests pass, with no failures in later modules (`test_tool.py`, `test_turn.py`, `test_utils.py`). A failure there would mean the `ToolRegistry` snapshot/restore leaked. If pytest is not found, run `uv run --extra dev pytest tests/`.

- [ ] **Step 2: Format**

Run the harness command:

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && uv run ruff format . && git status --porcelain
```

Expected: `git status` lists at most `pygents/registry.py` and `tests/unit/test_registry.py` as modified. About 16 files on main are already unformatted and are out of scope, so if `ruff format .` rewrote any other file, restore it:

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && git status --porcelain | awk '{print $2}' | grep -v -e '^pygents/registry.py$' -e '^tests/unit/test_registry.py$' | xargs -r git restore --
```

Then run `git status --porcelain` again and confirm that only the two in-scope files (or nothing) show as modified.

- [ ] **Step 3: Lint**

Run:

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && uv run ruff check .
```

Expected: no new findings in `pygents/registry.py` or `tests/unit/test_registry.py`, and in particular no F401 unused import for `TurnHook` or `hook`. If ruff is not found, run `uv run --extra dev ruff check .`.

- [ ] **Step 4: Re-run the suite if formatting changed anything, then commit**

If Step 2 changed either in-scope file:

```bash
cd /home/paulomtts/Code/pygents/.claude/worktrees/m1/task-unregister-one-registry-82dc9dcd && uv run pytest tests/ && git add pygents/registry.py tests/unit/test_registry.py && git commit -m "style(registry): ruff format unregister changes"
```

If nothing changed, skip the commit.
