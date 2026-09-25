<!-- task-pipeline: validated -->
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

---

# Document the Lifecycle Changes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update `docs/concepts/agents.md` and `docs/concepts/hooks.md` to describe the lifecycle fixes L1–L5 exactly as the code on this branch implements them, and keep the internal `docs/superpowers/` pages out of the published site.

**Architecture:** Docs-only. Each task has a RED check (a `grep` or a `mkdocs build` probe that fails on the current text), then exact `Edit`-ready replacements, then the same check passing (GREEN). The text is written from the code on this branch. Where that code is more precise than the spec, the docs follow the code, and the section "Code-wins discrepancies" below lists each case so the reviewer and the card report can mention it.

**Tech Stack:** MkDocs 1.6 + mkdocs-material (from the `dev` extra in `pyproject.toml`), Markdown with the `admonition` extension, `uv`, `pytest`, `ruff`.

**Spec:** `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-document-the-lifecycle-01c29ec1/docs/superpowers/specs/task-document-the-lifecycle-01c29ec1-design.md` (reproduced above).

**Worktree:** `/home/paulomtts/Code/pygents/.claude/worktrees/m1/task-document-the-lifecycle-01c29ec1` on branch `m1/task-document-the-lifecycle-01c29ec1`. Run every command from this directory. All paths below are relative to it.

## Global Constraints

- Only these files change: `docs/concepts/agents.md`, `docs/concepts/hooks.md`, `mkdocs.yml`.
- No code changes, no new tests, no new dependencies, no public API removed.
- Version stays `0.6.8` (`pyproject.toml`). Do not run bump2version. The docs may say the changes ship in 0.7.0; this plan does not add such a line.
- Tests live in `tests/unit/` next to the module's existing tests. This card adds none, so the rule does not apply.
- `ruff format` runs only on changed files. None are Python, so it is a no-op. Do NOT run `uv run ruff format .` over the repo (16 files on the base branch are already unformatted and reformatting them is out of scope).
- The `UnserializableHookError` message is quoted exactly: `hook 'log_turn' is not the one registered under that name (a closure or a duplicate); define it at module level to save its owner`
- There is no public `current_turn` attribute on `Agent`. The docs name the private `_current_turn` and the `to_dict()` key `"current_turn"` only.
- One commit, message exactly: `docs: lifecycle changes for 0.7.0`.
- Out of scope: `Agent.close()`/context-manager API, owner-qualified hook names or saving closures, re-queuing interrupted turns, the release, agent-manager work.

## Code-wins discrepancies (report these in the card summary)

The code on this branch (read at `pygents/agent.py:456-538`, `pygents/turn.py:242-371`, `pygents/registry.py:47-195`, `pygents/utils.py:27-51,203-233`, `pygents/errors.py:31-33`, and the behavior map at `tests/unit/test_agent.py:26-46`) is more precise than the spec in five places. The docs follow the code:

1. **Bare `break` is not an immediate close.** Python does not close an async generator when you `break` out of `async for`. A bare `break` only drops the generator, and asyncio's async-generator finalizer closes it on a later loop iteration (`tests/unit/test_agent.py` R11). Cleanup "right after the exit returns" (R10, R13) holds for `break` inside `contextlib.aclosing(agent.run())`, for `aclose()`, and for task cancellation. The docs recommend `aclosing` and say that until the finalizer runs, a new `run()` raises `SafeExecutionError`.
2. **A coroutine turn you `break` after has already completed.** `run()` yields a coroutine tool's value only after `returning()` has finished, so that turn keeps `StopReason.COMPLETED`. Only cancelling the task while the tool is awaited gives `CANCELLED`. In every early exit, `AFTER_TURN` does not fire for that turn.
3. **Task cancellation still raises `CancelledError` at whoever awaits the task.** That is normal asyncio. "No error" in the docs is scoped to `break` and `aclose()`, plus "nothing reaches the loop's exception handler".
4. **The first closure with a free name is registered.** `HookRegistry.wrap` (`pygents/registry.py:156-195`) registers the hook when its name is free, returns the existing wrapper for the same function, and only leaves the hook unregistered when a different object holds the name. So the first closure from a factory can be saved, and later ones cannot. The docs say this.
5. **Unregistering a hook makes owners that still hold it unsaveable.** `serialize_hooks_by_type` raises when nothing is registered under the name (`pygents/utils.py:214`), and `HookRegistry.unregister` leaves instance `.hooks` lists alone (`pygents/registry.py:101-111`). The docs say this.

Also: the spec summary handed to this planner was truncated at 2000 characters (it cut off at "agent-manager"). The spec itself was read in full from disk, and it is complete, so nothing was lost.

## Review Focus

1. A reader copies the "stop early" example with a bare `break` and immediately calls `run()` again. Expected: the docs steer them to `contextlib.aclosing` and warn that a bare `break` leaves the agent running until the loop finalizes the generator. Pinned by Task 2's RED/GREEN check for `aclosing` and `SafeExecutionError` inside the new section.
2. A reader retries a dropped turn by re-`put()`ting it. Expected: the docs show building a new `Turn` from the interrupted one (`Turn(interrupted.tool, args=..., kwargs=..., timeout=...)`), which `Turn.__init__` accepts (`pygents/turn.py:110-121`). Pinned by Task 2's check for `Turn(interrupted.tool`.
3. A reader uses a hook factory on two agents and saves one. Expected: the docs say the first closure is saveable, the second raises `UnserializableHookError` with the exact message, and they show the module-level fix. Pinned by Task 3's check for the exact message string.
4. A reader unregisters a global hook and expects it to stop everywhere. Expected: it stops firing globally, instance `.hooks` copies keep firing, and owners that hold it can no longer be saved. Pinned by Task 3's check for `HookRegistry.unregister`.
5. `mkdocs build` publishes `docs/superpowers/**` or fails on broken anchors to the new sections. Expected: no `superpowers` pages in `site/`, and the two cross-page anchors (`hooks.md#closures-and-reused-names`, `#stopping-run-early`) resolve. Pinned by Task 1's `find site` check and Task 4's build-log grep for anchor warnings.

## File Structure

- `mkdocs.yml`: site config. Adds a top-level `exclude_docs` key.
- `docs/concepts/agents.md`: the Agent page. Adds a "Stopping `run()` early" section and a checkpoint note under Hooks, fixes the hook name-clash warning, adds `unregister` to Registry, adds a save warning and a dropped-turn note to Serialization, and updates the Errors table.
- `docs/concepts/hooks.md`: the Hooks page. Fixes the name-clash warning, updates the `ON_COMPLETE` and `AFTER_TURN` rows, adds a "Closures and reused names" subsection, adds `unregister` to Registry, and updates the Errors table.

---

### Task 1: Exclude internal docs from the published site

**Files:**
- Modify: `mkdocs.yml:1-4` (add a key after `repo_name`)

**Interfaces:**
- Consumes: nothing.
- Produces: `site/` without `superpowers/` pages. Task 4 re-runs this check.

- [ ] **Step 1: Install the docs toolchain**

Run: `uv sync --dev --extra dev`
Expected: exits 0 and installs `mkdocs` and `mkdocs-material`. If it fails (for example, no network), record "docs toolchain could not be installed; mkdocs build skipped", skip Steps 2 and 5, and do Steps 3 and 4 anyway.

- [ ] **Step 2: RED — build and confirm internal pages are published**

Run: `rm -rf site && uv run mkdocs build 2>&1 | tail -n 20; find site -path '*superpowers*' -name 'index.html' | head`
Expected: FAIL for our purpose. `find` lists pages such as `site/superpowers/specs/task-document-the-lifecycle-01c29ec1-design/index.html`, and the build log shows INFO lines about pages under `superpowers/` that are not in `nav`.

- [ ] **Step 3: Add `exclude_docs`**

Edit `mkdocs.yml`. Replace:

```yaml
repo_url: https://github.com/paulomtts/pygents
repo_name: paulomtts/pygents
```

with:

```yaml
repo_url: https://github.com/paulomtts/pygents
repo_name: paulomtts/pygents

# Internal design specs and plans live under docs/superpowers/; never publish them.
exclude_docs: superpowers/
```

- [ ] **Step 4: Check the key is present**

Run: `grep -n '^exclude_docs: superpowers/$' mkdocs.yml`
Expected: one line, `7:exclude_docs: superpowers/`.

- [ ] **Step 5: GREEN — rebuild and confirm nothing internal is published**

Run: `rm -rf site && uv run mkdocs build; echo "exit=$?"; find site -path '*superpowers*' | wc -l`
Expected: `exit=0`, and `find ... | wc -l` prints `0`. `site/` is listed in `.gitignore` (line 13), so it never appears in `git status`.

---

### Task 2: agents.md — early exit, dropped turn, AFTER_TURN checkpoint, unregister, save error

**Files:**
- Modify: `docs/concepts/agents.md:184-261` (sections "Pausing and resuming" end, "Hooks", "Registry", "Serialization", "Errors")

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: the anchor `#stopping-run-early` (from the heading ``## Stopping `run()` early``). It links to `hooks.md#closures-and-reused-names`, which Task 3 creates. The link resolves once Task 3 is done, and Task 4 verifies it.

- [ ] **Step 1: RED — confirm the content is missing**

Run:

```bash
for s in 'Stopping `run()` early' 'contextlib.aclosing' 'Turn(interrupted.tool' 'StopReason.CANCELLED' 'dropped, not re-queued' 'AgentRegistry.unregister' 'UnregisteredToolError' 'UnserializableHookError' 'consistent checkpoints'; do
  grep -qF "$s" docs/concepts/agents.md && echo "PASS $s" || echo "FAIL $s"
done
```

Expected: every line starts with `FAIL`.

- [ ] **Step 2: Add the "Stopping `run()` early" section**

Edit `docs/concepts/agents.md`. Replace:

````markdown
restored.resume()                # now it will run
```

## Hooks
````

with:

````markdown
restored.resume()                # now it will run
```

## Stopping `run()` early

You can leave `run()` before the queue is empty: `break` out of the loop, call `aclose()` on the generator, or cancel the task that is iterating it. When you `break`, wrap the generator in `contextlib.aclosing` so the cleanup below has finished before your code continues:

```python
import contextlib

from pygents import Agent, Turn, tool

@tool()
async def stream_rows(n: int):
    for i in range(n):
        yield i

@tool()
async def work(x: int) -> int:
    return x * 2

agent = Agent("worker", "Streams rows", [stream_rows, work])
await agent.put(Turn("stream_rows", kwargs={"n": 100}))
await agent.put(Turn("work", kwargs={"x": 1}))

async with contextlib.aclosing(agent.run()) as stream:
    async for turn, value in stream:
        if value == 3:
            interrupted = turn
            break  # stream_rows is still running, so it is cancelled
```

What happens to the turn that was running:

- **Async generator tools.** `run()` closes the turn's generator before it restores anything else. The tool's producer task is cancelled and awaited, so the tool's own `finally` blocks run. The turn's `metadata.stop_reason` becomes `StopReason.CANCELLED`, and `ON_COMPLETE` fires with `StopReason.CANCELLED`. The agent's turn-scoped hooks (`@agent.on_complete`, etc.) are still attached at that moment, so they see it too.
- **Coroutine tools.** Cancelling the task while the tool is still being awaited inside `returning()` has the same result: `stop_reason` is `StopReason.CANCELLED` and `ON_COMPLETE` fires with it. A coroutine tool's value is yielded only after the tool has finished, so if you `break` after receiving it, that turn has already completed and keeps `StopReason.COMPLETED`.
- In every case, `AFTER_TURN` does not fire for the interrupted turn.

The same close happens if one of the agent's own hooks (for example `ON_TURN_VALUE`) raises while an async generator turn is paused at a value. The turn ends as `CANCELLED`, and the hook's exception reaches you.

**The interrupted turn is dropped, not re-queued.** The next `run()` starts at the head of the queue, which here is `work`. To retry the interrupted work, `put()` a new turn for it:

```python
await agent.put(
    Turn(interrupted.tool, args=interrupted.args, kwargs=interrupted.kwargs, timeout=interrupted.timeout)
)

async for turn, value in agent.run():
    ...  # runs work(x=1), then stream_rows(n=100) from the start
```

Leaving early does not raise: `break` and `aclose()` return normally, and nothing is reported to the event loop's exception handler. Cancelling a task works as usual in asyncio, so awaiting the cancelled task raises `CancelledError`. Once the exit has returned, the agent is idle and can be used right away. `run()` always clears its running state and its current turn on the way out, so `put()` and `run()` work immediately without `SafeExecutionError`.

!!! note "A bare `break`"
    Without `contextlib.aclosing`, `break` only drops the generator, because Python does not close an async generator when you leave `async for`. asyncio's async-generator finalizer closes it on a later loop iteration with the same result (turn `CANCELLED`, agent idle, no error). Until then the agent still counts as running, and calling `run()` again raises `SafeExecutionError`. Use `aclosing`, or call `aclose()` yourself, when you want to reuse the agent immediately.

## Hooks
````

- [ ] **Step 3: Update the `AFTER_TURN` row in the agent hook table**

Edit `docs/concepts/agents.md`. Replace:

```markdown
| `AFTER_TURN` | After turn fully processed | `(agent, turn)` |
```

with:

```markdown
| `AFTER_TURN` | After turn fully processed; the agent's `_current_turn` is already `None` | `(agent, turn)` |
```

- [ ] **Step 4: Add the checkpoint note after the agent hook table**

Edit `docs/concepts/agents.md`. Replace (this pair is unique: the other "Attach hooks after construction" line near the top is not preceded by a table row):

```markdown
| `ON_RESUME` | After the gate is released and before the next turn | `(agent)` |

Attach hooks after construction via method decorators or `agent.hooks.append(h)`:
```

with:

````markdown
| `ON_RESUME` | After the gate is released and before the next turn | `(agent)` |

`BEFORE_TURN` and `AFTER_TURN` are consistent checkpoints. In `BEFORE_TURN` the next turn has not started yet. In `AFTER_TURN` the finished turn has already been cleared: the agent's `_current_turn` is `None` (there is no public `current_turn` attribute). An `agent.to_dict()` snapshot taken in either hook therefore describes exactly the work still to do, and restoring it with `Agent.from_dict()` never runs a finished turn again.

```python
snapshots = []

@agent.after_turn
async def checkpoint(agent, turn):
    snapshots.append(agent.to_dict())  # snapshots[-1]["current_turn"] is None
```

Attach hooks after construction via method decorators or `agent.hooks.append(h)`:
````

- [ ] **Step 5: Fix the hook name-clash warning in the Hooks section**

Edit `docs/concepts/agents.md`. Replace:

```markdown
Hooks are registered in `HookRegistry` at decoration time. Use named functions so they serialize by name.

!!! warning "ValueError"
    Registering a *different* hook with a name already in use in `HookRegistry` raises `ValueError`. Re-registering the same hook under the same name is allowed.
```

with:

```markdown
Hooks are registered in `HookRegistry` at decoration time. Define them as module-level functions with unique names so the agent can be saved; see [Hooks — Closures and reused names](hooks.md#closures-and-reused-names).

!!! warning "ValueError"
    A global `@hook(...)` whose name is already taken by a *different* hook raises `ValueError`. Re-registering the same hook under the same name is allowed. Method decorators such as `@agent.after_turn` never raise on a name clash.
```

- [ ] **Step 6: Add `unregister` to the Registry section**

Edit `docs/concepts/agents.md`. Replace:

````markdown
agent = AgentRegistry.get("worker")  # lookup by name
AgentRegistry.clear()                # empty the registry (useful in tests)
```

!!! warning "ValueError"
    `AgentRegistry.register()` raises `ValueError` if an agent with the same name is already registered.
````

with:

````markdown
agent = AgentRegistry.get("worker")  # lookup by name
AgentRegistry.unregister("worker")   # remove one agent; the name can be reused
AgentRegistry.clear()                # empty the registry (useful in tests)
```

!!! warning "ValueError"
    `AgentRegistry.register()` raises `ValueError` if an agent with the same name is already registered.

`unregister(name)` only affects lookup by name. `send()` can no longer find the agent, and a new `Agent` (including one rebuilt with `Agent.from_dict()`) may take the name. The agent object itself keeps working. `ToolRegistry.unregister(name)` works the same way for tools: agents that already hold the tool keep using it.

!!! warning "UnregisteredAgentError / UnregisteredToolError"
    `AgentRegistry.unregister(name)` raises `UnregisteredAgentError` for an unknown name. `ToolRegistry.unregister(name)` raises `UnregisteredToolError`.
````

- [ ] **Step 7: Note the dropped turn and the save error in Serialization**

Edit `docs/concepts/agents.md`. Replace:

```markdown
The serialized form includes the queued turns, the `current_turn` if a turn was in-flight at serialize time (so it will be replayed on resume), and the full context pool and queue.
```

with:

```markdown
The serialized form includes the queued turns, the `current_turn` if a turn was in-flight at serialize time (so it will be replayed on resume; a turn interrupted by leaving `run()` early is dropped instead, see [Stopping `run()` early](#stopping-run-early)), and the full context pool and queue.
```

Then replace:

```markdown
!!! warning "UnregisteredHookError"
    `Agent.from_dict()` raises `UnregisteredHookError` if a hook name is not found in `HookRegistry`.
```

with:

```markdown
!!! warning "UnregisteredHookError"
    `Agent.from_dict()` raises `UnregisteredHookError` if a hook name is not found in `HookRegistry`.

!!! warning "UnserializableHookError"
    `agent.to_dict()` raises `UnserializableHookError` if the agent's hooks, its turn hooks, its context pool or its context queue hold a hook that is not the one registered under its name (a closure or a duplicate). See [Hooks — Closures and reused names](hooks.md#closures-and-reused-names).
```

- [ ] **Step 8: Update the Errors table**

Edit `docs/concepts/agents.md`. Replace:

```markdown
| `ValueError` | Tool instance mismatch, duplicate agent name, tool not in agent's set, or duplicate hook name |
| `SafeExecutionError` | Changing attributes or calling `run()` while already running or paused |
| `UnregisteredAgentError` | `send` target not found in `AgentRegistry` |
| `UnregisteredHookError` | Hook name not found in `HookRegistry` during `from_dict()` |
| `TurnTimeoutError` | A turn exceeds its timeout (propagated from the turn) |
```

with:

```markdown
| `ValueError` | Tool instance mismatch, duplicate agent name, tool not in agent's set, or a global `@hook` whose name is taken by a different hook |
| `SafeExecutionError` | Changing attributes or calling `run()` while already running or paused (including after a bare `break`, until the loop closes the generator) |
| `UnregisteredAgentError` | `send` target, or `AgentRegistry.unregister()` name, not found in `AgentRegistry` |
| `UnregisteredToolError` | `ToolRegistry.unregister()` name not found in `ToolRegistry` |
| `UnregisteredHookError` | Hook name not found in `HookRegistry` during `from_dict()` |
| `UnserializableHookError` | `to_dict()` while the agent holds a hook that is not the one registered under its name |
| `TurnTimeoutError` | A turn exceeds its timeout (propagated from the turn) |
```

- [ ] **Step 9: GREEN — re-run the check**

Run the same loop as Step 1.
Expected: every line starts with `PASS`.

---

### Task 3: hooks.md — closures and reused names, UnserializableHookError, HookRegistry.unregister

**Files:**
- Modify: `docs/concepts/hooks.md:40-62` (name-clash warning, `ON_COMPLETE` and `AFTER_TURN` rows), `:157-175` (insert a subsection before "Where hooks attach"), `:401-436` (Registry, Errors)

**Interfaces:**
- Consumes: nothing.
- Produces: the anchor `#closures-and-reused-names` (from the heading `### Closures and reused names`), which `agents.md` links to from Task 2.

- [ ] **Step 1: RED — confirm the content is missing**

Run:

```bash
for s in '### Closures and reused names' "hook 'log_turn' is not the one registered under that name (a closure or a duplicate); define it at module level to save its owner" 'UnserializableHookError' 'HookRegistry.unregister' 'clean, error, timeout, or cancelled' 'never raise on a name clash'; do
  grep -qF "$s" docs/concepts/hooks.md && echo "PASS $s" || echo "FAIL $s"
done
```

Expected: every line starts with `FAIL`.

- [ ] **Step 2: Fix the name-clash warning under "Defining hooks"**

Edit `docs/concepts/hooks.md`. Replace:

```markdown
Hooks are registered in `HookRegistry` at decoration time. The function name is the hook's identifier for lookup and serialization.

!!! warning "ValueError"
    Registering a *different* hook with a name already in use raises `ValueError`. Re-registering the same hook under the same name is allowed.
```

with:

```markdown
Hooks are registered in `HookRegistry` at decoration time. The function name is the hook's identifier for lookup and serialization.

!!! warning "ValueError"
    A global `@hook(...)` whose function name is already registered to a *different* hook raises `ValueError`. Re-registering the same hook under the same name is allowed. Instance method decorators (`@obj.before_invoke`, etc.) never raise on a name clash; see [Closures and reused names](#closures-and-reused-names).
```

- [ ] **Step 3: Update the `ON_COMPLETE` and `AFTER_TURN` rows**

Edit `docs/concepts/hooks.md`. Replace:

```markdown
| `ON_COMPLETE` | Always fires in finally block (clean, error, or timeout) | `(turn, stop_reason)` |
```

with:

```markdown
| `ON_COMPLETE` | Always fires in finally block (clean, error, timeout, or cancelled) | `(turn, stop_reason)` |
```

Then replace:

```markdown
| `AFTER_TURN` | After turn fully processed | `(agent, turn)` |
```

with:

```markdown
| `AFTER_TURN` | After turn fully processed; the agent's `_current_turn` is already `None` | `(agent, turn)` |
```

- [ ] **Step 4: Add the "Closures and reused names" subsection**

Edit `docs/concepts/hooks.md`. Replace:

````markdown
# When my_tool is invoked, both hooks fire:
# 1. instance_before  (instance list)
# 2. global_before    (global list, not a duplicate)
```

### Where hooks attach
````

with:

````markdown
# When my_tool is invoked, both hooks fire:
# 1. instance_before  (instance list)
# 2. global_before    (global list, not a duplicate)
```

### Closures and reused names

Instance method decorators accept any async function, including closures built by a factory and functions whose name is already registered. Attaching them never raises:

```python
from pygents import Agent

def make_logger(prefix: str):
    async def log_turn(agent, turn):
        print(f"{prefix} {turn.tool.metadata.name}")
    return log_turn

a = Agent("a", "desc", [my_tool])
b = Agent("b", "desc", [my_tool])

a.after_turn(make_logger("[a]"))  # name 'log_turn' is free: registered
b.after_turn(make_logger("[b]"))  # name taken by a different function: attached, not registered
```

Both hooks fire for their own agent. What happens to the name depends on who holds it:

| Name `fn.__name__` in `HookRegistry` | Result |
|---|---|
| Free | The hook is registered under that name |
| Held by a hook wrapping this same function | The existing hook is reused |
| Held by something else (a second closure, or another function with the same name) | The hook is attached to its owner only and is **not** registered |

Global `@hook(...)` is stricter: a name clash with a different hook raises `ValueError`.

**Saving an owner that holds an unregistered hook.** Hooks are saved by name and restored with `HookRegistry.get(name)`, so only the hook registered under a name can be saved. An owner (agent, turn, context queue, or context pool) that holds any other hook cannot be saved: `to_dict()` raises `UnserializableHookError`. It lives in `pygents.errors`, is exported from `pygents`, and subclasses `ValueError`. The message names the hook:

```text
hook 'log_turn' is not the one registered under that name (a closure or a duplicate); define it at module level to save its owner
```

```python
from pygents import UnserializableHookError

a.to_dict()  # fine: a holds the 'log_turn' that is registered
try:
    b.to_dict()
except UnserializableHookError as exc:
    print(exc)
```

To make the hook saveable, define it at module level with a unique name, and read per-owner values from the arguments instead of closing over them:

```python
async def log_turn(agent, turn):
    print(f"[{agent.name}] {turn.tool.metadata.name}")

a.after_turn(log_turn)
b.after_turn(log_turn)  # same function: both agents hold the one registered hook
```

### Where hooks attach
````

- [ ] **Step 5: Add `unregister` to the Registry section**

Edit `docs/concepts/hooks.md`. Replace:

````markdown
my_hook = HookRegistry.get("log_start")
HookRegistry.clear()  # empty the registry (useful in tests)
```

!!! warning "UnregisteredHookError"
    `HookRegistry.get(name)` raises `UnregisteredHookError` if no hook is registered with that name.
````

with:

````markdown
my_hook = HookRegistry.get("log_start")
HookRegistry.unregister("log_start")  # remove one hook; it also stops firing globally
HookRegistry.clear()  # empty the registry (useful in tests)
```

`unregister(name)` removes the hook from the registry and from the global hooks, so a global `@hook` stops firing everywhere and the name is free again. Objects that hold the hook in their own `.hooks` list keep it and keep firing it. Because nothing is registered under that name any more, such an owner can no longer be saved: `to_dict()` raises `UnserializableHookError`.

!!! warning "UnregisteredHookError"
    `HookRegistry.get(name)` and `HookRegistry.unregister(name)` raise `UnregisteredHookError` if no hook is registered with that name.
````

- [ ] **Step 6: Update the Errors table**

Edit `docs/concepts/hooks.md`. Replace:

```markdown
| `ValueError` | Empty type list, or duplicate hook name in `HookRegistry` |
| `UnregisteredHookError` | `HookRegistry.get()` with unknown name |
```

with:

```markdown
| `ValueError` | Empty type list, or a global `@hook` whose name is taken by a different hook |
| `UnregisteredHookError` | `HookRegistry.get()` or `HookRegistry.unregister()` with unknown name |
| `UnserializableHookError` | Saving (`to_dict()`) an owner that holds a hook which is not the one registered under its name |
```

- [ ] **Step 7: GREEN — re-run the check**

Run the same loop as Step 1.
Expected: every line starts with `PASS`.

---

### Task 4: Full verification and commit

**Files:**
- No new edits. Commits `docs/concepts/agents.md`, `docs/concepts/hooks.md`, `mkdocs.yml`.

**Interfaces:**
- Consumes: the anchors from Tasks 2 and 3 and the `exclude_docs` key from Task 1.
- Produces: one commit on `m1/task-document-the-lifecycle-01c29ec1`.

- [ ] **Step 1: Check only the three allowed files changed**

Run: `git status --short`
Expected: exactly these three lines (plus the untracked plan/spec under `docs/superpowers/` if they are not yet committed, which are left as they are):

```text
 M docs/concepts/agents.md
 M docs/concepts/hooks.md
 M mkdocs.yml
```

- [ ] **Step 2: Build the site and check anchors and exclusions**

Skip if Task 1 Step 1 could not install the toolchain, and say so in the card summary.
Run: `rm -rf site && uv run mkdocs build; echo "exit=$?"`
Expected: `exit=0`.

Run: `rm -rf site && uv run mkdocs build 2>&1 | grep -nE "WARNING|anchor|stopping-run-early|closures-and-reused-names"; find site -path '*superpowers*' | wc -l`
Expected: the `grep` prints no line that mentions `agents.md`, `hooks.md`, `stopping-run-early` or `closures-and-reused-names`, and `find ... | wc -l` prints `0`. (MkDocs 1.6 reports unknown anchors as INFO by default; if any line names one of the two anchors, fix the link text or heading so the slug matches and rebuild.)

- [ ] **Step 3: Run the test suite**

Run: `uv run --extra dev pytest tests/`
Expected: all tests pass, with the same count as before this card (no code changed).

- [ ] **Step 4: Lint**

Run: `uv run --extra dev ruff check .`
Expected: `All checks passed!`

Do not run `uv run ruff format .`. The changed files are Markdown and YAML, which ruff does not format, so formatting "only changed files" is a no-op.

- [ ] **Step 5: Confirm the version is unchanged**

Run: `grep -n '^version = ' pyproject.toml`
Expected: `7:version = "0.6.8"`

- [ ] **Step 6: Commit**

```bash
git add docs/concepts/agents.md docs/concepts/hooks.md mkdocs.yml
git commit -m "docs: lifecycle changes for 0.7.0" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NBto4a38sXMwEQEvaWTB5w"
```

Expected: one commit touching exactly three files. `git show --stat HEAD` lists `docs/concepts/agents.md`, `docs/concepts/hooks.md` and `mkdocs.yml` only.

- [ ] **Step 7: Report**

In the card summary, state whether the mkdocs build ran or was skipped, and list the five "Code-wins discrepancies" above as places where the docs follow the code rather than the spec's wording.
