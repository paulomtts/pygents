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
