<!-- task-pipeline: validated -->
# Subtask 49fa11af: Clear `current_turn` before AFTER_TURN hooks

Parent story: 12ce51ba "AFTER_TURN snapshots". Milestone design: `docs/superpowers/specs/2026-09-25-lifecycle-fixes-design.md`, decision L5. This subtask narrows L5 to its single code change; there are no sibling subtasks.

## Scope

- In `pygents/agent.py`, `Agent.run()`: move `self._current_turn = None` (currently line 532 in this worktree) so that it runs before `await self._run_hooks(AgentHook.AFTER_TURN, self, turn)` (currently line 531 in this worktree; both lines are near the bottom of the `while` loop body, right after the per-turn `try/finally` cleanup block). Line numbers differ from the milestone design's citation of 495/496 because this worktree's branch already includes Story 1's early-exit fix (the `turn_gen`/guarded-cleanup `finally` block), which shifted the file by 36 lines; use the statements' identity, not a fixed line number, when applying the change. Nothing else in `run()` changes.
- Update decision-table row R7 in the docstring at the top of `tests/unit/test_agent.py` to reflect the new order (clear `_current_turn`, then AFTER_TURN with the finished turn).
- Add the test(s) below to `tests/unit/test_agent.py`.

Out of scope: `Agent.close()` / context-manager API, early-exit handling (Story 1), owner-qualified hook names / closure hooks (Story 2), registry unregister (Story 3), docs (Story 5), re-queuing an interrupted turn, version bump or release (stays 0.6.8), agent-manager follow-up. No new dependencies, no public API removal.

## Observable behavior

- While an AFTER_TURN hook runs, `agent._current_turn is None` and `agent.to_dict()["current_turn"] is None`. The next pending turn, if any, is still at the head of `to_dict()["queue"]`. A snapshot taken in AFTER_TURN therefore describes the agent's resumable state correctly: the finished turn is not recorded as in progress, so restoring from it does not run that turn again.
- The AFTER_TURN hook still gets the finished `turn` as its explicit argument. Only the agent attribute changes, not the hook arguments.
- `_is_running` is still True during AFTER_TURN. Assigning `_current_turn` there is allowed because `__setattr__` exempts `_current_turn` from the running guard (`pygents/agent.py:72-73` in this worktree). This adds no new `SafeExecutionError` path and no ordering hazard.
- Loop termination does not change. The `while` condition runs after AFTER_TURN and sees `_current_turn is None` in both the old and new order. If an AFTER_TURN hook enqueues a turn, it is still picked up.

## Error paths

- If the tool raises (including `TurnTimeoutError`), the exception propagates as before, AFTER_TURN does not fire, and the outer `finally` clears `_current_turn` and `_is_running`. This is unchanged.
- If an AFTER_TURN hook raises, the exception propagates out of `run()` as before. The difference is that `_current_turn` is already `None` at that point. The outer `finally` still resets state.

## Tests (all in `tests/unit/test_agent.py`, the unit tier)

Placement rule: the spec's section 5 says "Tests live in tests/unit/ next to the module's existing tests". This is a behavior fix inside one module (`Agent.run`), not wiring across several components, so its tests go in the unit tier, not `tests/integration/`. Style: synchronous `def test_...(): asyncio.run(_body())` with an async body (no pytest-asyncio). Each test clears `AgentRegistry`, `ToolRegistry`, and `HookRegistry` itself, because the autouse fixture only clears `_global_hooks`.

1. **`test_after_turn_snapshot_has_no_current_turn`** (unit): enqueue two turns and register an AFTER_TURN hook that records `agent.to_dict()` and its `turn` argument. After the first turn finishes: the snapshot's `current_turn` is `None`, the snapshot's `queue` has the second turn at its head, and the hook's `turn` argument is the finished first turn (identity, with the output set).
2. **`test_after_turn_hook_receives_finished_turn_and_current_turn_cleared`** (unit): run a single turn. Inside AFTER_TURN, assert `agent._current_turn is None` and that the `turn` argument is the turn that just ran. After `run()` finishes, the agent is not running and `_current_turn` is `None`.

Existing R7/R8 tests must still pass unchanged.

## Verification

- fullSuite: `uv run pytest tests/`
- typecheck: none
- lint: `uv run ruff format .`, `uv run ruff check .`

Note: the milestone plan uses `uv run --extra dev ...` because pytest and ruff live in the dev extra. `ruff format` should run only on the files this subtask touches, not on the already-unformatted files on main.

---

# Clear `current_turn` before AFTER_TURN Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Agent.run()` clear `self._current_turn` before firing AFTER_TURN hooks, so a snapshot taken in AFTER_TURN does not record the finished turn as in progress.

**Architecture:** One statement moves in `pygents/agent.py` `Agent.run()`: `self._current_turn = None` goes from after `await self._run_hooks(AgentHook.AFTER_TURN, self, turn)` to before it. The hook still receives the local `turn` variable. Two unit tests in `tests/unit/test_agent.py` pin the behavior, and the R7 row of that file's decision-table docstring is updated.

**Tech Stack:** Python, asyncio, pytest (no pytest-asyncio), ruff, uv.

**Spec:** `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-clear-current-turn-49fa11af/docs/superpowers/specs/task-clear-current-turn-49fa11af-design.md` (reproduced verbatim above). Milestone design: `docs/superpowers/specs/2026-09-25-lifecycle-fixes-design.md`, decision L5.

## Global Constraints

- Only `Agent.run()` in `pygents/agent.py` changes in production code; tests go in `tests/unit/test_agent.py`.
- No new dependencies, no public API removal.
- Version stays 0.6.8 (no bump; release is a separate human step).
- Branch `m1/task-clear-current-turn-49fa11af`, worktree `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-clear-current-turn-49fa11af`. Do not assume any other subtask's code exists beyond what is already on this branch (the branch already contains Story 1's early-exit `finally` block in `run()`; do not modify it).
- Test style: synchronous `def test_...():` that calls `asyncio.run(_body())` on an async body. No `async def test_...`, no pytest-asyncio.
- Tests clear the registries they rely on themselves (`tests/conftest.py`'s autouse fixture clears only `HookRegistry._global_hooks`).
- Out of scope: `Agent.close()` / context-manager API, early-exit handling, owner-qualified hook names / closure hooks, registry unregister, docs, re-queuing an interrupted turn, version bump/release, agent-manager follow-up.
- Run all commands from the worktree root `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-clear-current-turn-49fa11af`. pytest and ruff live in the `dev` extra, so the working form is `uv run --extra dev ...`. Run `ruff format` only on files this subtask touches.

**Deviation from the spec (registry clearing):** the spec says each new test clears `AgentRegistry`, `ToolRegistry`, and `HookRegistry`. This plan clears `AgentRegistry` and `HookRegistry` only. Reason: `tests/unit/test_agent.py` registers its tools (`add_agent`, etc.) once at import via module-level `@tool()`; `Agent.__init__` rejects tools not in `ToolRegistry` (decision-table row I1). Calling `ToolRegistry.clear()` inside a test would unregister `add_agent` for this test and for every later test in the file, causing `UnregisteredToolError` across the suite. No existing test in this file clears `ToolRegistry`, and the file does not import it. Keeping `ToolRegistry` intact is what the spec's intent ("clear the registries the test uses by name so state does not leak") requires here.

## Review Focus

- AFTER_TURN hook that enqueues a new turn (via `await agent.put(...)`): the loop must still pick it up, since the `while` condition checks the queue after AFTER_TURN. Pinned by `test_after_turn_hook_enqueue_is_still_processed` in Task 1.
- AFTER_TURN hook that raises: the exception must propagate out of `run()` and the agent must end with `_is_running is False` and `_current_turn is None`. Pinned by `test_after_turn_hook_raises_propagates_and_agent_is_idle` in Task 1.
- Snapshot taken in AFTER_TURN restored via `Agent.from_dict`: the restored agent must run only the remaining queued turn, not re-run the finished one. Pinned by `test_after_turn_snapshot_restores_without_rerunning_finished_turn` in Task 1.
- Tool raising inside a turn: AFTER_TURN must not fire and state must reset. Already covered by existing tests `test_run_on_turn_error_hook_called` and `test_run_propagates_turn_timeout_error` (R5/R6/R8); no new test.
- Streaming (async-gen) tool: AFTER_TURN ordering is the same statement for both branches, so the coroutine-tool tests cover it; the existing `test_run_before_turn_after_turn_and_on_turn_value_hooks_called` keeps its ordering assertion. No new test.

---

### Task 1: Clear `_current_turn` before AFTER_TURN

**Files:**
- Modify: `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-clear-current-turn-49fa11af/pygents/agent.py:531-532` (inside `Agent.run()`, the last two statements of the `while` loop body)
- Modify: `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-clear-current-turn-49fa11af/tests/unit/test_agent.py:33` (decision-table row R7)
- Test: `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-clear-current-turn-49fa11af/tests/unit/test_agent.py` (insert new tests directly after `test_run_on_turn_value_hook_raises_propagates_and_cleans_up`, which ends at line 756, before `def test_run_early_break_coroutine_does_not_raise():` at line 759)

**Interfaces:**
- Consumes: existing `Agent`, `Turn`, `AgentHook`, `hook`, `AgentRegistry`, `HookRegistry`, module-level tool `add_agent(a: int, b: int) -> int` — all already imported/defined at the top of `tests/unit/test_agent.py`. `Agent.to_dict()` returns `"current_turn"` (dict or `None`) and `"queue"` (list of `Turn.to_dict()` dicts with keys `"tool_name"`, `"kwargs"`, `"output"`, ...). `Agent.from_dict(data)` restores queue and current turn.
- Produces: no new API. Behavior: during AFTER_TURN, `agent._current_turn is None`.

- [ ] **Step 1: Write the failing tests**

Insert this block in `tests/unit/test_agent.py` directly after the end of `test_run_on_turn_value_hook_raises_propagates_and_cleans_up` (after the line `    assert agent._is_running is False  # finally ran`) and before `def test_run_early_break_coroutine_does_not_raise():`:

```python
def test_after_turn_snapshot_has_no_current_turn():
    AgentRegistry.clear()
    HookRegistry.clear()
    snapshots = []
    hook_turns = []

    @hook(AgentHook.AFTER_TURN)
    async def snapshot_after_turn(agent, turn):
        snapshots.append(agent.to_dict())
        hook_turns.append(turn)

    agent = Agent("a", "desc", [add_agent])
    first = Turn("add_agent", kwargs={"a": 1, "b": 2})
    second = Turn("add_agent", kwargs={"a": 10, "b": 20})

    async def _body():
        await agent.put(first)
        await agent.put(second)
        async for _ in agent.run():
            pass

    asyncio.run(_body())

    assert len(snapshots) == 2
    first_snapshot = snapshots[0]
    assert first_snapshot["current_turn"] is None
    assert len(first_snapshot["queue"]) == 1
    assert first_snapshot["queue"][0]["tool_name"] == "add_agent"
    assert first_snapshot["queue"][0]["kwargs"] == {"a": 10, "b": 20}
    assert hook_turns[0] is first
    assert hook_turns[0].output == 3
    assert snapshots[1]["current_turn"] is None
    assert snapshots[1]["queue"] == []
    assert hook_turns[1] is second


def test_after_turn_hook_receives_finished_turn_and_current_turn_cleared():
    AgentRegistry.clear()
    HookRegistry.clear()
    seen = []

    @hook(AgentHook.AFTER_TURN)
    async def record_after_turn(agent, turn):
        seen.append((agent._current_turn, agent._is_running, turn))

    agent = Agent("a", "desc", [add_agent])
    only = Turn("add_agent", kwargs={"a": 4, "b": 5})

    async def _body():
        await agent.put(only)
        async for _ in agent.run():
            pass

    asyncio.run(_body())

    assert len(seen) == 1
    current_during_hook, running_during_hook, hook_turn = seen[0]
    assert current_during_hook is None
    assert running_during_hook is True
    assert hook_turn is only
    assert hook_turn.output == 9
    assert agent._is_running is False
    assert agent._current_turn is None


def test_after_turn_hook_enqueue_is_still_processed():
    AgentRegistry.clear()
    HookRegistry.clear()
    enqueued = []

    @hook(AgentHook.AFTER_TURN)
    async def enqueue_once(agent, turn):
        if not enqueued:
            follow_up = Turn("add_agent", kwargs={"a": 100, "b": 1})
            enqueued.append(follow_up)
            await agent.put(follow_up)

    agent = Agent("a", "desc", [add_agent])

    async def _body():
        await agent.put(Turn("add_agent", kwargs={"a": 1, "b": 1}))
        return [value async for _, value in agent.run()]

    values = asyncio.run(_body())

    assert values == [2, 101]
    assert agent._queue.empty()
    assert agent._current_turn is None


def test_after_turn_hook_raises_propagates_and_agent_is_idle():
    AgentRegistry.clear()
    HookRegistry.clear()
    seen_current = []

    @hook(AgentHook.AFTER_TURN)
    async def exploding_after_turn(agent, turn):
        seen_current.append(agent._current_turn)
        raise RuntimeError("after turn failed")

    agent = Agent("a", "desc", [add_agent])

    async def _body():
        await agent.put(Turn("add_agent", kwargs={"a": 1, "b": 2}))
        async for _ in agent.run():
            pass

    with pytest.raises(RuntimeError, match="after turn failed"):
        asyncio.run(_body())

    assert seen_current == [None]
    assert agent._is_running is False
    assert agent._current_turn is None


def test_after_turn_snapshot_restores_without_rerunning_finished_turn():
    AgentRegistry.clear()
    HookRegistry.clear()
    snapshots = []

    agent = Agent("a", "desc", [add_agent])

    @agent.after_turn
    async def snapshot_first_turn(agent, turn):
        if not snapshots:
            snapshots.append(agent.to_dict())

    async def _body():
        await agent.put(Turn("add_agent", kwargs={"a": 1, "b": 2}))
        await agent.put(Turn("add_agent", kwargs={"a": 10, "b": 20}))
        async for _ in agent.run():
            pass

    asyncio.run(_body())

    data = snapshots[0]
    data["hooks"] = {}
    AgentRegistry.clear()
    restored = Agent.from_dict(data)

    async def _resume():
        return [value async for _, value in restored.run()]

    assert asyncio.run(_resume()) == [30]
```

Note on the last test: it uses the instance-scoped `@agent.after_turn` decorator (`Agent.after_turn` at `pygents/agent.py:194-210`, which calls `build_method_decorator(AgentHook.AFTER_TURN, self.hooks, ...)`). Because `HookRegistry.clear()` ran first, `HookRegistry.wrap` registers `snapshot_first_turn` under its name, so `agent.to_dict()` inside the hook serializes it without `UnserializableHookError` (`pygents/utils.py:222`). The test then drops `hooks` from the snapshot before `from_dict` so the restored agent's behavior does not depend on hook rebuilding.

- [ ] **Step 2: Run the new tests to verify they fail**

Run:
```bash
uv run --extra dev pytest tests/unit/test_agent.py -v -k "after_turn_snapshot_has_no_current_turn or after_turn_hook_receives_finished_turn_and_current_turn_cleared or after_turn_hook_enqueue_is_still_processed or after_turn_hook_raises_propagates_and_agent_is_idle or after_turn_snapshot_restores_without_rerunning_finished_turn"
```
Expected:
- `test_after_turn_snapshot_has_no_current_turn` FAILS at `assert first_snapshot["current_turn"] is None` (it is the first turn's dict).
- `test_after_turn_hook_receives_finished_turn_and_current_turn_cleared` FAILS at `assert current_during_hook is None` (it is the `only` turn).
- `test_after_turn_hook_raises_propagates_and_agent_is_idle` FAILS at `assert seen_current == [None]` (it holds the turn).
- `test_after_turn_snapshot_restores_without_rerunning_finished_turn` FAILS: the restored agent re-runs the finished turn, returning `[3, 30]` instead of `[30]`.
- `test_after_turn_hook_enqueue_is_still_processed` PASSES already (regression guard for loop termination; it must keep passing after the change).

- [ ] **Step 3: Write the minimal implementation**

In `pygents/agent.py`, inside `Agent.run()`, at the end of the `while` loop body (right after the per-turn `finally:` block that ends with `raise cleanup_error`), replace:

```python
                await self._run_hooks(AgentHook.AFTER_TURN, self, turn)
                self._current_turn = None
```

with:

```python
                self._current_turn = None
                await self._run_hooks(AgentHook.AFTER_TURN, self, turn)
```

Keep the indentation (16 spaces). Do not touch anything else in `run()`, including the outer `finally:` that sets `self._is_running = False` and `self._current_turn = None`.

- [ ] **Step 4: Update decision-table row R7**

In `tests/unit/test_agent.py`, replace line 33:

```
  R7  After: AFTER_TURN; clear _current_turn
```

with:

```
  R7  After: clear _current_turn; then AFTER_TURN(agent, finished turn) - snapshots in the hook see current_turn None, next turn at queue head
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run:
```bash
uv run --extra dev pytest tests/unit/test_agent.py -v -k "after_turn_snapshot_has_no_current_turn or after_turn_hook_receives_finished_turn_and_current_turn_cleared or after_turn_hook_enqueue_is_still_processed or after_turn_hook_raises_propagates_and_agent_is_idle or after_turn_snapshot_restores_without_rerunning_finished_turn"
```
Expected: 5 passed.

- [ ] **Step 6: Run the full suite**

Run:
```bash
uv run --extra dev pytest tests/
```
Expected: all tests pass, including the existing R7/R8 tests (`test_run_before_turn_after_turn_and_on_turn_value_hooks_called`, `test_run_on_turn_error_hook_called`, `test_run_propagates_turn_timeout_error`) unchanged.

- [ ] **Step 7: Format and lint the touched files**

Run:
```bash
uv run --extra dev ruff format pygents/agent.py tests/unit/test_agent.py
uv run --extra dev ruff check .
```
Expected: format reports the files unchanged or reformatted with no errors; `ruff check` reports `All checks passed!`. If `ruff format` changed anything, rerun `uv run --extra dev pytest tests/unit/test_agent.py` and confirm it passes.

- [ ] **Step 8: Commit**

```bash
git add pygents/agent.py tests/unit/test_agent.py
git commit -m "fix(agent): clear current_turn before AFTER_TURN hooks

Snapshots taken inside AFTER_TURN no longer record the finished turn as
in progress, so restoring from them does not re-run it. The hook still
receives the finished turn as its argument.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NBto4a38sXMwEQEvaWTB5w"
```
