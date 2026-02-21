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
await alice.send_turn("bob", Turn("work", kwargs={"x": 42}))
```

`send_turn` looks up the target agent in `AgentRegistry` and calls `put()` on it.

!!! warning "UnregisteredAgentError"
    `send_turn` raises `UnregisteredAgentError` if the target agent name is not found in `AgentRegistry`.

---

The sections below cover branching, pausing, hooks, serialization, and other less commonly-used features. If you're getting started, the [Building an AI Agent](../guides/ai-agent.md) guide shows a complete working example.

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

## Hooks

Agent hooks fire at specific points during the run loop. Hooks are stored as a list and selected by type at run time. Exceptions in hooks propagate.

| Hook | When | Args |
|------|------|------|
| `BEFORE_TURN` | Before consuming next turn from queue | `(agent)` |
| `AFTER_TURN` | After turn fully processed | `(agent, turn)` |
| `ON_TURN_VALUE` | Before yielding each result | `(agent, turn, value)` |
| `ON_TURN_ERROR` | Turn raised an exception | `(agent, turn, exception)` |
| `ON_TURN_TIMEOUT` | Turn timed out | `(agent, turn)` |
| `BEFORE_PUT` | Before enqueueing a turn | `(agent, turn)` |
| `AFTER_PUT` | After enqueueing a turn | `(agent, turn)` |
| `ON_PAUSE` | When the run loop hits a paused gate | `(agent)` |
| `ON_RESUME` | After the gate is released and before the next turn | `(agent)` |

Use the `@hook(type)` decorator so the hook is registered and carries its type, then append it to `agent.hooks`:

```python
from pygents import Agent, AgentHook, hook

@hook(AgentHook.AFTER_TURN)
async def on_complete(agent, turn):
    print(f"[{agent.name}] {turn.tool.metadata.name} → {turn.stop_reason}")

agent = Agent("my_agent", "Description", [my_tool])
agent.hooks.append(on_complete)
```

Hooks are registered in `HookRegistry` at decoration time. Use named functions so they serialize by name.

!!! warning "ValueError"
    Registering a *different* hook with a name already in use in `HookRegistry` raises `ValueError`. Re-registering the same hook under the same name is allowed.

## Registry

Agents **auto-register** with `AgentRegistry` on construction. `send_turn` and `from_dict` use the registry to resolve agents by name.

```python
from pygents import AgentRegistry

agent = AgentRegistry.get("worker")  # lookup by name
AgentRegistry.clear()                # empty the registry (useful in tests)
```

!!! warning "ValueError"
    `AgentRegistry.register()` raises `ValueError` if an agent with the same name is already registered.

## Serialization

```python
data = agent.to_dict()       # name, description, tool_names, queue, current_turn, hooks, context_pool, context_queue, is_paused
agent = Agent.from_dict(data)  # rebuilds from registries, repopulates queue, pool, context_queue, and pause state
```

The serialized form includes the queued turns, the `current_turn` if a turn was in-flight at serialize time (so it will be replayed on resume), and the full context pool and queue. Hooks (agent-level, context pool, and context queue) are serialized by name and resolved from `HookRegistry` on deserialization. The `is_paused` field is also preserved — a paused agent reconstructed via `from_dict()` stays paused until `resume()` is called.

!!! warning "UnregisteredHookError"
    `Agent.from_dict()` raises `UnregisteredHookError` if a hook name is not found in `HookRegistry`.

## Errors

| Exception | When |
|-----------|------|
| `ValueError` | Tool instance mismatch, duplicate agent name, tool not in agent's set, or duplicate hook name |
| `SafeExecutionError` | Changing attributes or calling `run()` while already running or paused |
| `UnregisteredAgentError` | `send_turn` target not found in `AgentRegistry` |
| `UnregisteredHookError` | Hook name not found in `HookRegistry` during `from_dict()` |
| `TurnTimeoutError` | A turn exceeds its timeout (propagated from the turn) |
