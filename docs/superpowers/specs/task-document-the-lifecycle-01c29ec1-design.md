# Subtask 01c29ec1: Document the lifecycle changes (design)

Parent story: 72cb6467 "Documentation" (its only child). Milestone: 909eb7ad. Source of truth: `docs/superpowers/specs/2026-09-25-lifecycle-fixes-design.md` (L1–L6) and plan Task 5.1 in `docs/superpowers/plans/2026-09-25-lifecycle-fixes.md`. Blocked by story 12ce51ba (the L1–L5 code fixes); this subtask documents the post-fix code.

## Scope

Files touched (only these three): `docs/concepts/agents.md`, `docs/concepts/hooks.md`, `mkdocs.yml`. No code changes, no new tests, no version change (stays 0.6.8), no new dependencies.

Before writing, read `pygents/agent.py`, `pygents/turn.py`, `pygents/registry.py`, `pygents/utils.py`, and `pygents/errors.py` as they exist after the fix stories land, and describe what the code actually does. If the code disagrees with the list below, the code wins and the discrepancy is reported instead of being papered over.

## Required documentation content

`docs/concepts/agents.md`:
- Stopping `run()` early (breaking out of `async for`, closing the generator, or cancelling the task) while a turn is running: that turn is cancelled. Its stop reason is `StopReason.CANCELLED`, `ON_COMPLETE` fires with `CANCELLED`, and coroutine tools cancelled inside `returning()` get the same stop reason (L1).
- The interrupted turn is dropped, not re-queued. The next `run()` starts at the head of the queue. To retry the turn, call `put()` again. A plain early exit does not surface an error to the caller or to the event loop's exception handler. The agent can be reused right away because `_is_running` and `_current_turn` are always cleared (L1, L2).
- `AFTER_TURN` sees the agent's `_current_turn` (there is no public `current_turn` attribute) as `None`; a `to_dict()` snapshot taken there has `current_turn: None`. Together with `BEFORE_TURN`, this makes it a consistent point to checkpoint or serialize the agent (L5).
- `AgentRegistry.unregister(name)` removes one agent. An unknown name raises `UnregisteredAgentError`. The docs may say that `ToolRegistry.unregister` works the same way and raises `UnregisteredToolError` (L4).

`docs/concepts/hooks.md`:
- Instance hooks added with method decorators (`@obj.before_invoke`, etc.) may be closures or reuse a name that is already registered. This no longer raises. Such a hook is attached to its owner but is not registered by name. Global `@hook(...)` still raises `ValueError` on a name clash (L3). Update any existing text that says both paths raise `ValueError`.
- An owner that holds one of these hooks cannot be saved. Serialization raises `UnserializableHookError`, which lives in `pygents.errors` and is exported from `pygents`. Quote the message form exactly: `hook 'log_turn' is not the one registered under that name (a closure or a duplicate); define it at module level to save its owner`. The fix is to define the hook at module level with a unique name (L3).
- `HookRegistry.unregister(name)` removes the hook from the registry and from the global hooks. An unknown name raises `UnregisteredHookError` (L4).

`mkdocs.yml`: add `exclude_docs: superpowers/` so the internal specs and plans are not published.

## Out of scope

- `Agent.close()` or a context-manager API.
- Owner-qualified hook names, or saving closures.
- Re-queuing an interrupted turn.
- The 0.7.0 version bump and release. That is a human step after merge. The docs may say the changes ship in 0.7.0, but no files are bumped.
- agent-manager follow-up work.
- Reformatting files this subtask did not touch.

## Error paths documented

`UnserializableHookError` on save; `ValueError` on a global `@hook` name clash; `UnregisteredAgentError`, `UnregisteredToolError`, and `UnregisteredHookError` from `unregister` on unknown names. No error for a plain early exit from `run()`.

## Verification

- If the docs toolchain installs, run `uv sync --dev --extra dev`, then `uv run mkdocs build`. The build must succeed and `site/` must contain no `superpowers/` pages. If the toolchain cannot be installed, skip this step and say so explicitly.
- `uv run --extra dev pytest tests/` stays green (unchanged, since there are no code edits).
- `uv run --extra dev ruff check .` stays green. `ruff format` applies only to changed files, and since none are Python it is effectively a no-op.
- Commit message: `docs: lifecycle changes for 0.7.0`.

## Tests

None added. This subtask only changes docs and is verified by the mkdocs build. The placement rule ("Tests live in `tests/unit/` next to the module's existing tests", from the plan's Global Constraints) does not apply here because there are no tests. Nothing goes in `tests/unit/` or `tests/integration/`.
