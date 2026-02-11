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
- `run()` — async generator: consumes turns from the queue, runs them, yields `(turn, value)`, exits when queue is empty

!!! warning "ValueError"
    `put(turn)` raises `ValueError` if the turn has no tool or the tool is not in the agent's set.

**After each turn:**

| Condition | Behavior |
|-----------|----------|
| Queue is empty | Exit loop |
| Output is a `Turn` instance | Enqueue it, continue |
| Otherwise | Continue to next turn |

## Streaming

Agents stream by default. The `run()` method is an async generator that yields `(turn, value)` pairs as results are produced — not batched at the end. This means you can process partial results, update UI, or make decisions before a long-running sequence completes.

Single-value tools yield once per turn. Async generator tools yield per value. The agent detects the tool type and calls `returning()` or `yielding()` automatically. The loop exits when the queue is empty.

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

## Immutability while running

While `run()` is active, agent attributes cannot be changed. Calling `run()` again while already running is also not allowed.

!!! warning "SafeExecutionError"
    Changing agent attributes or calling `run()` while the agent is already running raises `SafeExecutionError`.

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

Use the `@hook(hook_type)` decorator so the hook is registered and carries its type, then append it to `agent.hooks`:

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

## Serialization

```python
data = agent.to_dict()       # name, description, tool_names, queue, hooks
agent = Agent.from_dict(data)  # rebuilds from registries, repopulates queue
```

Hooks are serialized by name and resolved from `HookRegistry` on deserialization.

!!! warning "UnregisteredHookError"
    `Agent.from_dict()` raises `UnregisteredHookError` if a hook name is not found in `HookRegistry`.
