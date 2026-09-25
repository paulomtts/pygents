# Lifecycle fixes — design

Date: 2026-09-25
Fixes brd issues `628ebf70`, `b46c27da`, `d27e5006`, `43343bae` (all reproduced on 0.6.8, `main` `f9722e2`, and 0.6.7 on PyPI).
Status: milestone 1 scope; decisions L1–L6. Ships as 0.7.0.

## 1. Why

These surfaced while designing agent-manager on pygents (one `Agent` per unit of work, checkpointed with `to_dict()`, restored with `from_dict()`, created by factories, and consumers that stop reading `run()` early). Each has a workaround there; each would trip any user.

| Issue | Symptom |
|---|---|
| `628ebf70` | Leaving `async for ... in agent.run()` early (break, `aclose()`, task cancelled) logs an unhandled `SafeExecutionError`, leaves the agent flagged running, and leaves the tool running in a background task. |
| `b46c27da` | Two agents built by the same factory with an instance hook (`@agent.after_turn` on a closure) → `ValueError: 'log_turn' already registered`. |
| `d27e5006` | No way to remove one agent (or tool, or hook) from its registry; a name stays taken for the process's life. |
| `43343bae` | `to_dict()` inside an `AFTER_TURN` hook lists the finished turn as `current_turn`; restoring that snapshot re-runs it. |

## 2. What was found

- `Agent.run()` (`agent.py` ~444-499) iterates `turn.yielding()` with `async for`. When the consumer stops at `yield (turn, value)`, Python throws `GeneratorExit` into `run()`, but the inner `turn.yielding()` generator is **not** closed then (breaking an `async for` does not `aclose()` its iterator; it is finalized later). `run()`'s inner `finally` then does `turn.hooks = original_hooks` while the turn is still `_is_running`, and `Turn.__setattr__` raises `SafeExecutionError`; the context-var resets after that line in the same `finally` are skipped.
- `Turn.yielding()` (`turn.py` ~279-355) runs the tool in a `producer` task feeding a queue. Its handlers cover timeout and `Exception`; `GeneratorExit`/`CancelledError` reach only the `finally`, which runs `ON_COMPLETE` with `stop_reason` still `None` and never cancels `producer`, so the tool keeps running. `StopReason.CANCELLED` exists and is unused on this path.
- Observed after an early exit: `agent._is_running` is still `True` (reproduced with the `aclose()` script in issue `628ebf70`); the exact path is part of Task 1.2's diagnosis.
- Instance hook decorators all go through `utils.build_method_decorator` → `HookRegistry.wrap`, which registers the hook by `__name__`; `BaseRegistry.register` raises on a different object under an existing name (`registry.py` ~31-38). Hooks are saved by name (`utils.serialize_hooks_by_type`) and restored with `HookRegistry.get(name)` (`utils.rebuild_hooks_from_serialization`). A closure's captured values are never saved, so a name cannot restore a closure.
- Global `@hook(...)` registers through `HookRegistry.register_global` (name registry + `_global_hooks`).
- Registries (`registry.py`) expose `register`, `get`, `clear`; no removal of one entry.
- In `run()`, `AFTER_TURN` fires before `self._current_turn = None` (~495-496).

## 3. Decisions

**L1 — Early exit closes the turn first.** When `run()` receives `GeneratorExit` or `CancelledError` while a turn is in flight, it closes that turn's generator (`aclose()`) before any other cleanup. The turn then cancels its `producer` task, awaits it, sets `stop_reason = StopReason.CANCELLED`, fires `ON_COMPLETE(CANCELLED)`, and clears its running flag. Only then does `run()` restore `turn.hooks` and reset the context vars, each step guarded so one failing cannot skip the next, and its outer `finally` always clears `_is_running` and `_current_turn`. Coroutine tools cancelled mid-`returning()` get the same `CANCELLED` stop reason.

**L2 — The interrupted turn is dropped.** It is not re-queued; the next `run()` starts at the queue head. Callers wanting a replay `put()` it again. No error reaches the caller or the event loop's exception handler for a plain early exit.

**L3 — Instance hooks attach freely; unsaveable ones refuse to save.** `HookRegistry.wrap` (the instance-decorator path) no longer raises on a name clash: it registers the hook by name only when the name is free or already holds the same function; otherwise the hook is attached to its owner without being registered. `serialize_hooks_by_type` raises a new `UnserializableHookError` (in `pygents.errors`, exported from `pygents`) when a hook is not the object registered under its name: `"hook 'log_turn' is not the one registered under that name (a closure or a duplicate); define it at module level to save its owner"`. Global `@hook(...)` still raises on a clash.

**L4 — `unregister(name)` on every registry.** `BaseRegistry.unregister(name)` removes one entry and raises the registry's not-found error (`UnregisteredAgentError` / `UnregisteredToolError` / `UnregisteredHookError`) for an unknown name. `HookRegistry.unregister` also removes the hook from `_global_hooks`. No `Agent.close()` (not needed yet).

**L5 — `current_turn` is cleared before `AFTER_TURN`.** `run()` sets `self._current_turn = None` before firing `AFTER_TURN`; hooks still receive the finished `turn` argument. A `to_dict()` there has `current_turn: None` and the next turn at the queue head.

**L6 — Release 0.7.0.** Two observable changes (the `AFTER_TURN` ordering; saving can raise `UnserializableHookError` where constructing used to raise `ValueError`) make it a minor bump (pre-1.0). `docs/concepts/agents.md` and `docs/concepts/hooks.md` document L1–L5. Released the usual way, by a human after the milestone is merged into `main` (the task workflow never pushes): `bump2version minor`, push, GitHub release `v0.7.0`, which runs `publish.yml` to PyPI. `main` is at 0.6.8 and PyPI at 0.6.7, so 0.7.0 also publishes what 0.6.8 held.

## 4. Changes by file

| File | Change |
|---|---|
| `pygents/agent.py` | `run()`: close the in-flight turn on `GeneratorExit`/`CancelledError`, guarded cleanup, always clear flags (L1, L2); clear `_current_turn` before `AFTER_TURN` (L5) |
| `pygents/turn.py` | `yielding()`: handle `GeneratorExit`/`CancelledError` (cancel and await `producer`, `CANCELLED`); `returning()`: `CANCELLED` on cancellation (L1) |
| `pygents/registry.py` | `BaseRegistry.unregister`; `HookRegistry.unregister` also edits `_global_hooks` (L4); `HookRegistry.wrap` stops raising on a clash (L3) |
| `pygents/utils.py` | `serialize_hooks_by_type` raises `UnserializableHookError` (L3) |
| `pygents/errors.py`, `pygents/__init__.py` | `UnserializableHookError` |
| `docs/concepts/agents.md`, `docs/concepts/hooks.md` | behaviour docs (L6) |
| `mkdocs.yml` | `exclude_docs: superpowers/` so these internal specs and plans are not published |

## 5. Testing

Everything in `tests/unit` (the existing layout), `uv run pytest tests/`; lint with `uv run ruff format .` and `uv run ruff check .` as CI does.

- Early exit (L1, L2), parametrised over async-generator and coroutine tools and over `break`, explicit `aclose()`, and cancelling the consuming task: afterwards `agent._is_running is False`, `agent._current_turn is None`, the turn's `stop_reason is StopReason.CANCELLED`, its hooks equal what they were before the agent added its turn hooks, the context vars are back to their prior values, the tool's `producer` task is done (the tool stopped: a tool that would append to a list after the exit point appends nothing), `ON_COMPLETE` fired once with `CANCELLED`, a new `run()` after `put()` works, and the loop's exception handler recorded nothing.
- Hooks (L3): two agents from one factory both construct and run; `to_dict()` of the first succeeds, of the second raises `UnserializableHookError` naming `log_turn`; a module-level instance hook still round-trips through `to_dict`/`from_dict`; two global `@hook` functions with one name still raise `ValueError`.
- Registries (L4): register → unregister → register again under the same name; unknown name raises each registry's not-found error; an unregistered global hook no longer fires.
- `AFTER_TURN` (L5): snapshots taken there have `current_turn is None` and the next turn at the queue head; the hook's `turn` argument is the finished turn.

## 6. Out of scope

- An `Agent.close()` or context-manager API.
- Owner-qualified hook names or saving closures.
- Re-queuing the interrupted turn.
- agent-manager's follow-up (raising its `pygents` floor to `>=0.7.0` and dropping its workarounds) belongs to that repo.
