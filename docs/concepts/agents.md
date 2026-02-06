# Agents

## Role

An **agent** is an orchestrator: it has a queue of turns and a set of tools. `run()` is an async generator that repeatedly pops a turn, runs it (using `returning()` or `yielding()` as appropriate), and yields `(turn, value)` for each result. The loop stops when a completion-check tool returns `True`. Agents stream by default—you consume results with `async for turn, value in agent.run(): ...`.

## Creating an agent

```python
from app import Agent, ToolRegistry, tool, Turn

@tool()
async def step_one(): ...
@tool()
async def step_done() -> bool: ...

agent = Agent("worker", "Processes steps", [step_one, step_done])
```

The constructor checks that each tool in the list is the same instance as in `ToolRegistry`; otherwise it raises `ValueError`. The agent registers itself in `AgentRegistry`.

## Queue and run loop

- **`put(turn)`** — Enqueues a turn. Fails if the turn has no tool or the tool name is not in the agent’s tool set. Runs `BEFORE_PUT` / `AFTER_PUT` hooks.
- **`pop()`** — Blocks until a turn is available and returns it.
- **`run()`** — Loop: `BEFORE_TURN` → pop → run turn (yielding each `(turn, value)`) → `AFTER_TURN`. Then:
  - If the turn was a completion-check and `output is True`, exit the loop.
  - If `output` is a `Turn` instance, enqueue it with `put(output)`.
  - Otherwise continue to the next iteration.

If a turn raises `TurnTimeoutError` or any other exception, the corresponding agent hooks run and the exception is re-raised from `run()`.

## Sending turns to other agents

`send_turn(agent_name, turn)` looks up the agent by name in `AgentRegistry` and calls `put(turn)` on it. Use this to hand off work to another agent.

## Immutability while running

While `run()` is active, agent attributes (other than `_is_running`) cannot be changed; `__setattr__` raises `SafeExecutionError`. Starting `run()` again while already running also raises `SafeExecutionError`.

## Serialization

`agent.to_dict()` returns `name`, `description`, `tool_names`, and `queue` (list of turn dicts). `Agent.from_dict(data)` rebuilds the agent from the registry by tool names, repopulates the queue with `Turn.from_dict()`, and registers the agent. Hooks are not serialized.
