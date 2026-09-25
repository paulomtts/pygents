# Lifecycle fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Each task is one `brd` subtask card in this repo, driven by the `task` workflow; the card's description names its Task and carries its excerpt.

**Goal:** Fix pygents' four lifecycle issues (early exit from `run()`, closure hooks colliding, no registry removal, stale `current_turn` in `AFTER_TURN`) and document them, ready to release as 0.7.0.

**Architecture:** Cancellation is handled where it starts: `Turn.yielding()`/`returning()` turn a `GeneratorExit`/`CancelledError` into a clean `CANCELLED` stop (producer cancelled), and `Agent.run()` closes its in-flight turn before any other cleanup. Hook saving becomes the place that judges whether a hook can be saved; attaching never raises. Registries gain `unregister`.

**Tech Stack:** Python ≥3.12, pytest, ruff; `uv`. No pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-25-lifecycle-fixes-design.md` (L1–L6).

## Global Constraints

- Verification for every task: `uv run --extra dev pytest tests/` green and `uv run --extra dev ruff check .` passes (pytest and ruff live in the `dev` extra). Run `uv run --extra dev ruff format <the files you changed>` on your own files only: 16 files on `main` are already unformatted, and reformatting them is out of scope.
- **Test style:** this repo has no async tests and no pytest-asyncio. Every test is a plain `def test_...()` that runs its body with `asyncio.run(...)` (the existing convention, see `tests/conftest.py`'s `collect_async`). The snippets below show `async def test_...` for brevity: implement each as `def test_...(): asyncio.run(_body())` with the snippet as `_body`. Do not add pytest-asyncio.
- Tests live in `tests/unit/` next to the module's existing tests; `tests/conftest.py` clears `HookRegistry._global_hooks` only, so tests that register agents/tools/hooks by name clear `AgentRegistry` / `ToolRegistry` / `HookRegistry` themselves (existing pattern).
- No new dependencies. No public API removed. Version stays 0.6.8 in every task (the release is a human step after merge, spec L6).
- Branch prefix `m1`; base `main`.

## Review Focus

1. **Cancelling the consuming task while a coroutine tool is awaiting** (not just async-generator tools). Expect `CANCELLED`, flags cleared, `CancelledError` still propagating to whoever cancelled. Test in Task 1.1 and 1.2.
2. **A tool that ignores cancellation for a while** (awaits in a `try/finally` that sleeps). `aclose()` must still complete once the tool finishes cleanup, and nothing is left running. Test in Task 1.1.
3. **`put()` then `run()` again right after an early exit.** The agent must be reusable immediately, not after garbage collection. Test in Task 1.2.
4. **A hook object attached to two owners** (the same module-level function on two agents). It is the registered one, so both save. Test in Task 2.1.
5. **`unregister` of a tool still referenced by a live agent.** The agent keeps its tool object; only lookup by name fails (`Turn("name")` raises `UnregisteredToolError`). Test in Task 3.1.

---

## Story 1 — Early exit from `run()`

### Task 1.1: A turn that is closed or cancelled stops cleanly

**Files:** Modify `pygents/turn.py`. Test: `tests/unit/test_turn.py`.

**Interfaces:** `Turn.yielding()` and `Turn.returning()`: on `GeneratorExit` (yielding) or `asyncio.CancelledError` (both), cancel and await the `producer` task (yielding), set `metadata.stop_reason = StopReason.CANCELLED`, let the existing `finally` fire `ON_COMPLETE(CANCELLED)` and clear `_is_running`, then re-raise. Nothing else changes.

- [ ] **Step 1: Failing tests**

```python
async def test_closing_a_yielding_turn_cancels_its_tool():
    after_exit = []
    @tool()
    async def slow_gen():
        yield 1
        await asyncio.sleep(0.05)
        after_exit.append("ran")          # must never happen after close
        yield 2
    turn = Turn(slow_gen)
    gen = turn.yielding()
    assert await gen.__anext__() == 1
    await gen.aclose()
    await asyncio.sleep(0.1)
    assert after_exit == []
    assert turn.metadata.stop_reason is StopReason.CANCELLED
    assert turn._is_running is False

async def test_on_complete_fires_once_with_cancelled(): ...       # record ON_COMPLETE args → [CANCELLED]
async def test_cancelling_a_returning_turn_marks_it_cancelled():  # Review Focus 1
    @tool()
    async def slow() -> int:
        await asyncio.sleep(10); return 1
    turn = Turn(slow)
    task = asyncio.create_task(turn.returning())
    await asyncio.sleep(0.01); task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert turn.metadata.stop_reason is StopReason.CANCELLED and turn._is_running is False

async def test_a_tool_slow_to_clean_up_still_finishes_closing(): ...  # Review Focus 2: finally with sleep(0.02)
```

Clear `ToolRegistry` around each test as `test_turn.py` already does (tool names are global). Write the `...` bodies in the same style.

- [ ] **Step 2:** Watch them fail (`stop_reason` is `None`; `after_exit == ["ran"]`).
- [ ] **Step 3: Implement** — in `yielding()`, wrap the queue loop:

```python
            try:
                while True:
                    ...                                   # unchanged
                await producer
            except (GeneratorExit, asyncio.CancelledError):
                producer.cancel()
                try:
                    await producer
                except (asyncio.CancelledError, Exception):
                    pass
                self.metadata.stop_reason = StopReason.CANCELLED
                raise
            except (asyncio.TimeoutError, TimeoutError) as exc:
                ...                                       # unchanged
```

Inside a `GeneratorExit` handler an `await` is allowed only because this is an async generator being closed with `aclose()` (Python runs the handler as part of `aclose`); do not `yield` there. In `returning()`, add `except asyncio.CancelledError: self.metadata.stop_reason = StopReason.CANCELLED; raise` before the generic `except Exception`.

- [ ] **Step 4:** Verification (Global Constraints) green.
- [ ] **Step 5: Commit** — `git commit -m "fix(turn): a closed or cancelled turn stops its tool and reports CANCELLED"`

### Task 1.2: Leaving `agent.run()` early leaves the agent reusable

**Files:** Modify `pygents/agent.py`. Test: `tests/unit/test_agent.py`.

**Interfaces:** `Agent.run()`: the per-turn body keeps a reference to the turn's generator (`turn_gen = turn.yielding()`); on `GeneratorExit`/`CancelledError` it `await turn_gen.aclose()` (or lets the cancelled `returning()` finish) **before** restoring `turn.hooks`; the hook restore and each context-var reset are separate guarded steps; the outer `finally` clears `_is_running` and `_current_turn`. The interrupted turn is not re-queued (L2). First diagnose why `_is_running` stayed `True` in the issue's `aclose()` repro and cover that path with its own test.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize("exit_by", ["break", "aclose", "cancel"])
@pytest.mark.parametrize("kind", ["gen", "coro"])
async def test_early_exit_leaves_the_agent_clean(exit_by, kind):
    # a gen tool yields twice with a sleep between; a coro tool sleeps before returning
    # exit after the first value (gen) or while awaiting (coro, via cancel only)
    ...
    assert agent._is_running is False and agent._current_turn is None
    assert turn.metadata.stop_reason is StopReason.CANCELLED
    assert turn.hooks == hooks_before_agent_added_turn_hooks
    assert _current_context_queue.get() is queue_before and _current_context_pool.get() is pool_before
    assert loop_errors == []                     # loop.set_exception_handler(lambda l, c: loop_errors.append(c))

async def test_run_again_right_after_an_early_exit():   # Review Focus 3
    ...  # break after the first value; put(Turn(...)); a second run() yields that turn's values
```

Skip the (`coro`, `break`/`aclose`) combinations with `pytest.skip` (a coroutine tool yields one value at the end; there is no "after the first value" point).

- [ ] **Step 2:** Watch them fail.
- [ ] **Step 3: Implement** per Interfaces; keep `ON_TURN_VALUE`/routing behaviour unchanged for runs that complete.
- [ ] **Step 4:** Verification green.
- [ ] **Step 5: Commit** — `git commit -m "fix(agent): leaving run() early closes the turn and leaves the agent reusable"`

---

## Story 2 — Closure hooks

### Task 2.1: Instance hooks attach freely; unsaveable ones refuse to save

**Files:** Modify `pygents/registry.py` (`HookRegistry.wrap`), `pygents/utils.py` (`serialize_hooks_by_type`), `pygents/errors.py`, `pygents/__init__.py`. Test: `tests/unit/test_hooks.py`, `tests/unit/test_utils.py`.

**Interfaces:** `class UnserializableHookError(ValueError)` in `pygents.errors`, exported from `pygents`. `HookRegistry.wrap(fn, ...)`: builds the `Hook`; registers it only if `fn.__name__` is free or holds a hook wrapping the same `fn`; otherwise returns the new, unregistered hook (no exception). `serialize_hooks_by_type(hooks)`: for each hook, `HookRegistry._registry.get(name) is hook` must hold, else raise `UnserializableHookError(f"hook {name!r} is not the one registered under that name (a closure or a duplicate); define it at module level to save its owner")`. `hook(...)` (global) and `register_global` unchanged: a clash still raises `ValueError`.

- [ ] **Step 1: Failing tests**

```python
def test_two_agents_from_one_factory_both_construct_and_the_second_refuses_to_save():
    def make(name):
        agent = Agent(name, "d", [noop])
        @agent.after_turn
        async def log_turn(agent, turn): pass
        return agent
    one, two = make("one"), make("two")                # no ValueError
    one.to_dict()                                      # the registered hook: saves
    with pytest.raises(UnserializableHookError, match="log_turn"):
        two.to_dict()

async def test_both_factory_agents_fire_their_own_hook(): ...      # each closure fires for its own agent only
def test_a_module_level_instance_hook_still_round_trips(): ...    # to_dict → from_dict → same hook object
def test_one_module_level_hook_on_two_agents_saves_twice(): ...    # Review Focus 4
def test_two_global_hooks_with_one_name_still_raise(): ...         # ValueError, unchanged
```

- [ ] **Step 2:** Watch them fail.  **Step 3: Implement.**  **Step 4:** Verification green.
- [ ] **Step 5: Commit** — `git commit -m "fix(hooks): closures attach freely; saving one raises UnserializableHookError"`

---

## Story 3 — Registry removal

### Task 3.1: `unregister(name)` on every registry

**Files:** Modify `pygents/registry.py`. Test: `tests/unit/test_registry.py`.

**Interfaces:** `BaseRegistry.unregister(cls, name: str) -> None`: removes the entry; unknown → `cls._not_found_error(f"{name!r} not found")`. `HookRegistry.unregister` also removes that hook object from `_global_hooks`.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize("registry, make, error", [
    (AgentRegistry, lambda n: Agent(n, "d", [noop]), UnregisteredAgentError),
    (ToolRegistry, make_tool_named, UnregisteredToolError),
    (HookRegistry, make_hook_named, UnregisteredHookError),
])
def test_unregister_frees_the_name(registry, make, error):
    make("x"); registry.unregister("x"); make("x")     # no ValueError
    with pytest.raises(error):
        registry.unregister("nope")

async def test_an_unregistered_global_hook_no_longer_fires(): ...
async def test_unregistering_a_tool_leaves_live_agents_working(): ...   # Review Focus 5
```

- [ ] **Step 2:** fail.  **Step 3:** implement.  **Step 4:** Verification green.
- [ ] **Step 5: Commit** — `git commit -m "feat(registry): unregister one entry by name"`

---

## Story 4 — `AFTER_TURN` snapshots

### Task 4.1: Clear `current_turn` before `AFTER_TURN`

**Files:** Modify `pygents/agent.py`. Test: `tests/unit/test_agent.py`.

**Interfaces:** in `run()`, `self._current_turn = None` moves above `await self._run_hooks(AgentHook.AFTER_TURN, self, turn)`.

- [ ] **Step 1: Failing test**

```python
async def test_after_turn_snapshot_has_no_current_turn():
    @tool()
    async def step(n: int):
        if n < 2:
            yield Turn(step, kwargs={"n": n + 1})
        yield n
    agent = Agent("snap", "d", [step]); seen = []
    @agent.after_turn
    async def snap(agent, turn):
        d = agent.to_dict()
        seen.append((turn.kwargs["n"], d["current_turn"], [t["kwargs"]["n"] for t in d["queue"]]))
    await agent.put(Turn(step, kwargs={"n": 0}))
    async for _ in agent.run(): pass
    assert seen == [(0, None, [1]), (1, None, [2]), (2, None, [])]
```

- [ ] **Step 2:** fail (`current_turn` is the finished turn).  **Step 3:** move the line.  **Step 4:** Verification green.
- [ ] **Step 5: Commit** — `git commit -m "fix(agent): current_turn is cleared before AFTER_TURN hooks"`

---

## Story 5 — Documentation

### Task 5.1: Document the lifecycle changes

**Files:** Modify `docs/concepts/agents.md`, `docs/concepts/hooks.md`, `mkdocs.yml` (add `exclude_docs: superpowers/`).

- [ ] **Step 1:** Read `pygents/agent.py`, `turn.py`, `registry.py`, `utils.py` as built; document from the code.
- [ ] **Step 2:** `agents.md`: stopping `run()` early (the turn is cancelled and dropped, `CANCELLED`, the agent is immediately reusable); `AFTER_TURN` sees `current_turn` as `None`, so it is a consistent checkpoint point as well as `BEFORE_TURN`; `AgentRegistry.unregister`. `hooks.md`: instance hooks may be closures; an owner holding one cannot be saved (`UnserializableHookError`), and how to make a hook saveable (module level); `unregister` on `HookRegistry`.
- [ ] **Step 3:** `uv run mkdocs build` (if the docs toolchain is installable: `uv sync --dev --extra dev`, else skip and say so) shows no `superpowers/` pages in `site/`; verification green.
- [ ] **Step 4: Commit** — `git commit -m "docs: lifecycle changes for 0.7.0"`

After the milestone is merged into `main`, a human releases it (spec L6): `bump2version minor` → push → `gh release create v0.7.0` → PyPI via `publish.yml`.
