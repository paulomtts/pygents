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
