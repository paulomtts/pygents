# Leaving `agent.run()` early leaves the agent reusable (c12afa98)

Parent story: 9e421692 "Early exit from run()" (milestone 909eb7ad). Blocked by 4554b946 "A turn that is closed or cancelled stops cleanly" (done). Spec of record: `docs/superpowers/specs/2026-09-25-lifecycle-fixes-design.md` (decisions L1, L2, L5). Plan: `docs/superpowers/plans/2026-09-25-lifecycle-fixes.md`, Story 1 Task 2.

Note: the exploration summary feeding this spec was truncated at 8000 characters (mid-sentence in the file:line references, around `pygents/utils.py` `safe_execution`). Nothing below depends on the missing text; the utils.py state was re-checked directly (see Prerequisite).

## Prerequisite

This branch must contain the sibling's `safe_execution` fix (`async with contextlib.aclosing(func(self, *args, **kwargs)) as agen:` in `pygents/utils.py` asyncgen wrapper, commit cba4cdb). It is present in this worktree (`pygents/utils.py:38`). It must also contain the sibling's `Turn.yielding()`/`Turn.returning()` CANCELLED handling in `pygents/turn.py`. This subtask consumes those behaviours and does not modify `pygents/turn.py` or `pygents/utils.py`.

## Scope

Only `Agent.run()` in `pygents/agent.py` (currently lines 443-499), plus tests in `tests/unit/test_agent.py`.

Changes to `run()`:

- Keep a reference to the turn's generator: `turn_gen = turn.yielding()` and iterate it, instead of the inline `async for value in turn.yielding():`.
- On `GeneratorExit` / `asyncio.CancelledError` in the per-turn body, `await turn_gen.aclose()` before the per-turn cleanup, so the turn's own generator finalizes (and records `StopReason.CANCELLED` via the sibling's turn.py logic) while the agent's turn hooks are still in place and before `turn.hooks` is restored. For the coroutine-tool path (`turn.returning()`), do not add a second close; let the cancelled `returning()` finish, since it already reports CANCELLED.
- Split the inner `finally` into independently guarded steps: (1) `turn.hooks = original_hooks`; (2) reset `_current_context_queue` (fallback to `prev_queue` on `ValueError`); (3) reset `_current_context_pool` (fallback to `prev_pool` on `ValueError`). A failure in one step must not skip the others. Today a single block runs the hook restore first and the two resets share one `try`, so a failure in one step skips the rest.
- The outer `finally` stays the final safety net and always sets `_is_running = False` and `_current_turn = None`.
- The interrupted turn is not re-queued (L2). The next `run()` starts at the queue head.
- L5 ordering is kept for normal completion: AFTER_TURN fires, then `self._current_turn = None`. Per the spec of record, `_current_turn` should be `None` before AFTER_TURN fires. The current code at lines 495-496 does the reverse, and fixing that belongs to the task that owns L5, not this card. This card must not change that ordering in either direction.
- For runs that complete normally, routing, ON_TURN_VALUE and yielded `(turn, value)` behaviour does not change.

## Observable behaviour

After the consumer leaves `run()` early (by `break` out of `async for`, by `await gen.aclose()`, or by cancelling the task driving it), right after that exit returns and with no GC or extra loop iterations needed:

- `agent._is_running is False` and `agent._current_turn is None`.
- The interrupted turn has `turn.metadata.stop_reason is StopReason.CANCELLED`.
- `turn.hooks` equals the list it had before the agent added its `turn_hooks`.
- `_current_context_queue` and `_current_context_pool` are back to the values they had before the run.
- No exception reaches the caller for a plain early exit. For a task cancel, only the expected `CancelledError` reaches the caller. Nothing reaches the event loop's exception handler.
- The agent can be reused at once: `put()` a new turn and call `run()` again.

## Error paths

- If restoring hooks or resetting a context var raises, the remaining cleanup steps still run, and `_is_running` / `_current_turn` are still cleared by the outer `finally`.
- Non-cancellation exceptions from tools (including `TurnTimeoutError`) still propagate as today. The new close logic applies only to `GeneratorExit` / `CancelledError`.

## Diagnosis to capture

The issue's `aclose()` repro left `_is_running` True for two reasons:

- The `@safe_execution` wrapper did not close the inner `run()` generator, so its cleanup was deferred to the loop's asyncgen finalizer. The sibling already fixed this.
- Inside `run()`, the inner `turn.yielding()` generator was never explicitly `aclose()`'d before the `finally` restored `turn.hooks`. This subtask fixes that part.

A dedicated test must pin this down: with `aclose()`, the state is clean right after `aclose()` returns.

## Tests

All tests go in the unit tier, in `tests/unit/test_agent.py`. The milestone spec's §5 Testing says all tests live in `tests/unit`, and the plan's Global Constraints say tests sit next to the module's existing tests. `tests/integration/` is not used.

Tests follow repo style: sync `def test_...()` wrapping `asyncio.run(_body())`, with no pytest-asyncio. Each test clears `AgentRegistry` / `ToolRegistry` / `HookRegistry` itself if it registers things by name.

1. `test_early_exit_leaves_the_agent_clean` (unit):
   - Parametrized over `kind` in {`"coro"`, `"stream"`} and `exit_by` in {`"break"`, `"aclose"`, `"cancel"`}.
   - The combinations (`coro`, `break`) and (`coro`, `aclose`) call `pytest.skip`, because coroutine tools yield only once at the end, so there is no point after the first value to exit at.
   - Asserts every item under Observable behaviour. For the loop-handler check, it records `loop.set_exception_handler` calls and expects `loop_errors == []`.
2. `test_early_exit_by_aclose_is_clean_immediately` (unit): the diagnosis regression. It covers the stream tool with `aclose()`, and asserts `_is_running is False`, `_current_turn is None` and restored `turn.hooks` right after `aclose()` returns, with no `gc.collect()` or `asyncio.sleep(0)` in between. If the parametrized test already asserts this at the same point, merge this into it and name it explicitly in its docstring rather than duplicating.
3. `test_run_again_right_after_an_early_exit` (unit): break after the first value, `put()` a new `Turn`, then call `run()` again right away. It completes normally and yields the new turn's values, and the interrupted turn is not re-run (L2).
4. `test_early_exit_cleanup_steps_are_independent` (unit): force the hook-restore step to raise, for example with a `turn.hooks` setter or a list stand-in that raises. Then assert that both context vars are still restored and that `_is_running is False` / `_current_turn is None`.
5. Existing `test_run_early_break_coroutine_does_not_raise` (test_agent.py:743) and `test_run_early_break_streaming_does_not_raise` (test_agent.py:757): replace them with the parametrized test (1), which covers the same assertions and more. If either one checks something (1) does not, such as the coroutine-with-break case the parametrization skips, keep that one test as is.
6. Existing normal-completion tests for ON_TURN_VALUE, routing and AFTER_TURN must still pass unchanged.

Verification: `uv run --extra dev pytest tests/` green and `uv run --extra dev ruff check .` passes (pytest and ruff live in the `dev` extra, per `pyproject.toml`). Run `uv run --extra dev ruff format` on the files you changed only.

## Out of scope

- `Agent.close()` or a context-manager API.
- Owner-qualified hook names or saving closures (L3).
- Registry `unregister` (L4).
- Any change to AFTER_TURN ordering (L5).
- Re-queuing the interrupted turn.
- Changes to `pygents/turn.py` or `pygents/utils.py`.
- A version bump (stays 0.6.8).
- Agent-manager follow-ups.
- New dependencies or removal of any public API.

## Commit

`fix(agent): leaving run() early closes the turn and leaves the agent reusable` on an `m1`-prefixed branch.
