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

## Queue and run loop

```python
await agent.put(Turn("work", kwargs={"x": 5}))
await agent.put(Turn("work", kwargs={"x": 10}))

async for turn, value in agent.run():
    print(f"{turn.tool_name}: {value}")
    # work: 10
    # work: 20
    # (then loop exits when queue is empty)
```

- `put(turn)` — enqueues a turn (validates tool is in agent's set)
- `pop()` — blocks until a turn is available
- `run()` — async generator: pops turns, runs them, yields `(turn, value)`, exits when queue is empty

**After each turn:**

| Condition | Behavior |
|-----------|----------|
| Queue is empty | Exit loop |
| Output is a `Turn` instance | Enqueue it, continue |
| Otherwise | Continue to next turn |

## Streaming

Single-value tools yield once. Async generator tools yield per value. The agent detects the tool type and calls `returning()` or `yielding()` automatically.

## Inter-agent messaging

```python
alice = Agent("alice", "Coordinator", [coordinate])
bob = Agent("bob", "Worker", [work])

# alice sends work to bob
await alice.send_turn("bob", Turn("work", kwargs={"x": 42}))
```

`send_turn` looks up the target agent in `AgentRegistry` and calls `put()` on it.

## Immutability while running

While `run()` is active, agent attributes cannot be changed — `__setattr__` raises `SafeExecutionError`. Calling `run()` again while already running also raises `SafeExecutionError`.

## Serialization

```python
data = agent.to_dict()       # name, description, tool_names, queue
agent = Agent.from_dict(data)  # rebuilds from registry, repopulates queue
```

Hooks are not serialized.
