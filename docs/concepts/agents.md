# Agents

An agent orchestrates execution: it owns a queue of turns and a set of tools, processes turns in order, and streams results.

## Creating an agent

```python
from pygents import Agent, tool, Turn

@tool()
async def work(x: int) -> int:
    return x * 2

agent = Agent("worker", "Doubles numbers", [work])
```

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `name` | required | Unique name; registered in `AgentRegistry` |
| `description` | required | Free-text description |
| `tools` | required | Tools the agent may run |
| `context_pool` | `None` | Pre-configured `ContextPool` (or subclass) to use; creates a default `ContextPool()` if not provided (see [Context](context.md#contextpool)) |
| `context_queue` | `None` | Pre-configured `ContextQueue` to use; creates a default `ContextQueue(limit=10)` if not provided (see [Context](context.md#contextqueue)) |
| `tags` | `None` | A list or frozenset of strings. Labels this agent so global `@hook` declarations with a matching `tags=` filter will fire for it. See [Hooks — Tag filtering](hooks.md#tag-filtering). |

Attach hooks after construction via method decorators or `agent.hooks.append(h)`:

```python
@agent.after_turn
async def on_complete(agent, turn):
    print(f"[{agent.name}] {turn.tool.metadata.name} → {turn.metadata.stop_reason}")
```

Each tool must be the same instance as in `ToolRegistry` — the constructor validates this.

!!! warning "ValueError"
    The constructor raises `ValueError` if a tool instance differs from the one in `ToolRegistry`.

## Queue and run loop

```python
await agent.put(Turn("work", kwargs={"x": 5}))
await agent.put(Turn("work", args=[10]))

async for turn, value in agent.run():
    print(f"{turn.tool.metadata.name}: {value}")
    # work: 10
    # work: 20
    # (then loop exits when queue is empty)
```

- `put(turn)` — enqueues a turn (validates tool is in agent's set)
- `run()` — async generator: consumes turns from the queue, runs them, yields `(turn, value)` as results are produced (not batched), exits when queue is empty. Because results are yielded as produced, you can process partial output, update a UI, or make decisions before a long sequence completes.

!!! warning "ValueError"
    `put(turn)` raises `ValueError` if the turn has no tool or the tool is not in the agent's set.

!!! warning "TurnTimeoutError"
    If a turn exceeds its timeout during `run()`, `TurnTimeoutError` propagates out of the generator.

**Value routing:**

Each value produced by a turn is routed before the next value (or the next turn) is started:

| Value type | Behavior |
|------------|----------|
| `Turn` | Enqueued via `put()` and executed in the same `run()` call; **not yielded to the caller** |
| `ContextItem` with `id=None` | Appended to `agent.context_queue`; **not yielded to the caller** |
| `ContextItem` with `id` set | Stored in `agent.context_pool`; **not yielded to the caller** |
| Anything else | Yielded to the caller as `(turn, value)`; no routing side-effect |

!!! info "Why Turn and ContextItem are consumed, not yielded"
    The routing table is what makes tools composable. A tool that returns a `Turn` drives the next step without knowing anything about the queue. A tool that returns a `ContextItem` accumulates state without knowing anything about the pool. The agent is the only thing that sees these types — callers of `run()` only ever receive plain values. This keeps implementation, declaration, and orchestration separate: tools don't reach into the queue or pool directly; the agent handles that from the return value's type alone.

For single-value tools, the returned value is routed once after the turn completes. For async generator tools, each yielded value is routed individually at the moment the consumer resumes — so a generator can yield a mix of `ContextItem`, `Turn`, and plain values in a single turn.

## Inter-agent messaging

```python
alice = Agent("alice", "Coordinator", [coordinate])
bob = Agent("bob", "Worker", [work])

# alice sends work to bob
await alice.send("bob", Turn("work", kwargs={"x": 42}))
```

`send` looks up the target agent in `AgentRegistry` and calls `put()` on it.

!!! warning "UnregisteredAgentError"
    `send` raises `UnregisteredAgentError` if the target agent name is not found in `AgentRegistry`.

---

The sections below cover branching, pausing, hooks, serialization, and other less commonly-used features. If you're getting started, the [Building a Research Assistant](../guides/research-assistant.md) guide shows a complete working example.

## Branching

Like `ContextQueue`, agents support branching. A child agent inherits the parent's configuration and queue, then diverges independently:

```python
parent = Agent("coordinator", "Main agent", [work, report])

await parent.put(Turn("work", kwargs={"x": 5}))

# Branch inherits description, tools, hooks, and queued turns
child = parent.branch("worker-1")

# Override any defaults
child2 = parent.branch(
    "worker-2",
    description="Specialized worker",
    tools=[work],       # subset of tools
    hooks=[],           # no hooks
)
```

| Parameter | Default | Behavior |
|-----------|---------|----------|
| `name` | required | Unique name for the child (registered in `AgentRegistry`) |
| `description` | parent's | Override with a string |
| `tools` | parent's | Override with a list |
| `hooks` | parent's | Pass `hooks=[]` for no hooks, or a new list to override |

The child inherits the parent's `tags`. Tags are also preserved through `to_dict()`/`from_dict()`.

The parent's queue is copied (non-destructively) to the child. The parent's `context_pool` and `context_queue` are both branched into the child — the child starts with a snapshot of the parent's context items and copies of the parent's hooks. Both agents are fully independent after branching — enqueueing or running turns on one does not affect the other.

```python
# Parent and child can run the same queued turns independently
async for turn, value in child.run():
    print(value)

async for turn, value in parent.run():
    print(value)
```

## Immutability while running or paused

While `run()` is active, agent attributes cannot be changed. Calling `run()` again while already running is also not allowed. The same restriction applies while the agent is paused — attributes are locked until `resume()` is called.

!!! warning "SafeExecutionError"
    Changing agent attributes or calling `run()` while the agent is running or paused raises `SafeExecutionError`.

## Pausing and resuming

An agent can be paused between turns. When paused, the run loop waits at the top of its iteration — the current turn always completes normally. `pause()` and `resume()` are safe to call at any time (including before or during `run()`), and both are idempotent.

```python
agent = Agent("worker", "Processes jobs", [work])

await agent.put(Turn("work", kwargs={"x": 1}))
await agent.put(Turn("work", kwargs={"x": 2}))

agent.pause()  # can be called before run() starts

async def collect():
    async for turn, value in agent.run():
        print(value)
        agent.pause()  # pause after each turn

task = asyncio.create_task(collect())
await asyncio.sleep(0.1)   # first turn finishes, loop is now gated

agent.resume()             # unblock next turn
await task
```

| Method / Property | Description |
|---|---|
| `agent.pause()` | Clear the gate; the run loop will block before its next turn |
| `agent.resume()` | Set the gate; the run loop resumes immediately |
| `agent.is_paused` | `True` while the gate is cleared |

A paused agent can be serialized and restored. `to_dict()` includes `is_paused`; `from_dict()` restores the paused state so the reconstructed agent will wait at the gate until `resume()` is called.

```python
agent.pause()
data = agent.to_dict()

restored = Agent.from_dict(data)
assert restored.is_paused        # still paused after round-trip

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

Agent hooks fire at specific points during the run loop. Hooks are stored as a list and selected by type at run time. Exceptions in hooks propagate.

| Hook | When | Args |
|------|------|------|
| `BEFORE_TURN` | Before consuming next turn from queue | `(agent)` |
| `AFTER_TURN` | After turn fully processed; the agent's `_current_turn` is already `None` | `(agent, turn)` |
| `ON_TURN_VALUE` | After routing (value already stored in `context_queue`/`context_pool`), before yielding to the caller | `(agent, turn, value)` |
| `BEFORE_PUT` | Before enqueueing a turn | `(agent, turn)` |
| `AFTER_PUT` | After enqueueing a turn | `(agent, turn)` |
| `ON_PAUSE` | When the run loop hits a paused gate | `(agent)` |
| `ON_RESUME` | After the gate is released and before the next turn | `(agent)` |

`BEFORE_TURN` and `AFTER_TURN` are consistent checkpoints. In `BEFORE_TURN` the next turn has not started yet. In `AFTER_TURN` the finished turn has already been cleared: the agent's `_current_turn` is `None` (there is no public `current_turn` attribute). An `agent.to_dict()` snapshot taken in either hook therefore describes exactly the work still to do, and restoring it with `Agent.from_dict()` never runs a finished turn again.

```python
snapshots = []

@agent.after_turn
async def checkpoint(agent, turn):
    snapshots.append(agent.to_dict())  # snapshots[-1]["current_turn"] is None
```

Attach hooks after construction via method decorators or `agent.hooks.append(h)`:

```python
from pygents import Agent

agent = Agent("my_agent", "Description", [my_tool])

@agent.after_turn
async def on_complete(agent, turn):
    print(f"[{agent.name}] {turn.tool.metadata.name} → {turn.metadata.stop_reason}")
```

For process-wide hooks that fire for every agent, use the global `@hook(AgentHook.*)` decorator:

```python
from pygents import hook, AgentHook

@hook(AgentHook.AFTER_TURN)
async def log_all(agent, turn):
    print(f"[{agent.name}] {turn.tool.metadata.name} → {turn.metadata.stop_reason}")
```

Hooks are registered in `HookRegistry` at decoration time. Define them as module-level functions with unique names so the agent can be saved; see [Hooks — Closures and reused names](hooks.md#closures-and-reused-names).

!!! warning "ValueError"
    A global `@hook(...)` whose name is already taken by a *different* hook raises `ValueError`. Re-registering the same hook under the same name is allowed. Method decorators such as `@agent.after_turn` never raise on a name clash.

## Registry

Agents **auto-register** with `AgentRegistry` on construction. `send` and `from_dict` use the registry to resolve agents by name.

```python
from pygents import AgentRegistry

agent = AgentRegistry.get("worker")  # lookup by name
AgentRegistry.unregister("worker")   # remove one agent; the name can be reused
AgentRegistry.clear()                # empty the registry (useful in tests)
```

!!! warning "ValueError"
    `AgentRegistry.register()` raises `ValueError` if an agent with the same name is already registered.

`unregister(name)` only affects lookup by name. `send()` can no longer find the agent, and a new `Agent` (including one rebuilt with `Agent.from_dict()`) may take the name. The agent object itself keeps working. `ToolRegistry.unregister(name)` works the same way for tools: agents that already hold the tool keep using it.

!!! warning "UnregisteredAgentError / UnregisteredToolError"
    `AgentRegistry.unregister(name)` raises `UnregisteredAgentError` for an unknown name. `ToolRegistry.unregister(name)` raises `UnregisteredToolError`.

## Serialization

```python
data = agent.to_dict()       # name, description, tool_names, queue, current_turn, hooks, context_pool, context_queue, is_paused
agent = Agent.from_dict(data)  # rebuilds from registries, repopulates queue, pool, context_queue, and pause state
```

The serialized form includes the queued turns, the `current_turn` if a turn was in-flight at serialize time (so it will be replayed on resume; a turn interrupted by leaving `run()` early is dropped instead, see [Stopping `run()` early](#stopping-run-early)), and the full context pool and queue. Hooks (agent-level, context pool, and context queue) are serialized by name and resolved from `HookRegistry` on deserialization. The `is_paused` field is also preserved — a paused agent reconstructed via `from_dict()` stays paused until `resume()` is called.

!!! warning "UnregisteredHookError"
    `Agent.from_dict()` raises `UnregisteredHookError` if a hook name is not found in `HookRegistry`.

!!! warning "UnserializableHookError"
    `agent.to_dict()` raises `UnserializableHookError` if the agent's hooks, its turn hooks, its context pool or its context queue hold a hook that is not the one registered under its name (a closure or a duplicate). See [Hooks — Closures and reused names](hooks.md#closures-and-reused-names).

## Errors

| Exception | When |
|-----------|------|
| `ValueError` | Tool instance mismatch, duplicate agent name, tool not in agent's set, or a global `@hook` whose name is taken by a different hook |
| `SafeExecutionError` | Changing attributes or calling `run()` while already running or paused (including after a bare `break`, until the loop closes the generator) |
| `UnregisteredAgentError` | `send` target, or `AgentRegistry.unregister()` name, not found in `AgentRegistry` |
| `UnregisteredToolError` | `ToolRegistry.unregister()` name not found in `ToolRegistry` |
| `UnregisteredHookError` | Hook name not found in `HookRegistry` during `from_dict()` |
| `UnserializableHookError` | `to_dict()` while the agent holds a hook that is not the one registered under its name |
| `TurnTimeoutError` | A turn exceeds its timeout (propagated from the turn) |
