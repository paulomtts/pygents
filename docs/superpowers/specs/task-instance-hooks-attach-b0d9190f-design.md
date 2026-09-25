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
