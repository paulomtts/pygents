# pygents

Async agent orchestration for Python. Four abstractions:

- **Tools** define _how_ — async functions decorated with `@tool`
- **Turns** define _what_ — which tool to run with what arguments
- **Agents** _orchestrate_ — process a queue of turns and stream results
- **Working Memory** provides _context_ — bounded, branchable window of state

## Install

```bash
pip install pygents
```

Requires Python 3.12+.

## Minimal example

```python
import asyncio
from pygents import Agent, Turn, tool

@tool()
async def fetch(url: str) -> str:
    return f"<content from {url}>"

async def main():
    agent = Agent("fetcher", "Fetches URLs", [fetch])
    await agent.put(Turn("fetch", kwargs={"url": "https://example.com"}))

    async for turn, value in agent.run():
        print(f"{turn.tool.metadata.name}: {value}")

asyncio.run(main())
```

## Tool-driven flow control

The key pattern in pygents is that **tools can return `Turn` objects**. When a tool returns a `Turn`, the agent automatically enqueues it for execution. This lets tools decide what happens next — not external orchestration code.

```python
@tool()
async def decide_next_step(context: str) -> str | Turn:
    if needs_more_info(context):
        return Turn("gather_info", kwargs={"context": context})
    return "Done processing"
```

This is powerful because:

- **Tools control flow** — the logic lives where the domain knowledge is
- **Chaining is implicit** — no explicit queue management needed
- **Conditional branching** — tools can choose different paths based on results

## Dynamic arguments

Callable positional args and kwargs are evaluated when the tool runs, not when the turn is created:

```python
config = {"retries": 3}

turn = Turn(
    "fetch",
    args=["https://example.com"],
    kwargs={
        "retries": lambda: config["retries"],  # read at runtime
    },
)
```

## Streaming

Tools can be async generators. The agent yields each value as it's produced:

```python
@tool()
async def count(n: int):
    for i in range(1, n + 1):
        yield i

agent = Agent("counter", "Counts", [count])
await agent.put(Turn("count", kwargs={"n": 3}))

async for turn, value in agent.run():
    print(value)  # 1, 2, 3
```

## Inter-agent messaging

Agents can enqueue turns on other agents:

```python
alice = Agent("alice", "Delegates", [delegate_tool])
bob = Agent("bob", "Works", [work_tool])

# alice sends a turn to bob's queue
await alice.send_turn("bob", Turn("work_tool", kwargs={"x": 42}))
```

## Capabilities

| Feature | Description |
|---------|-------------|
| Streaming | Agents yield results as produced via `async for turn, value in agent.run()` |
| Inter-agent messaging | `agent.send_turn(name, turn)` enqueues work on another agent |
| Dynamic arguments | Callable positional args and kwargs evaluated at invocation time |
| Timeouts | Per-turn timeout (default 60s), raises `TurnTimeoutError` |
| Per-tool locking | `@tool(lock=True)` serializes concurrent runs |
| Hooks | Async callbacks at turn, agent, and tool level |
| Serialization | `to_dict()` / `from_dict()` for turns, agents, and memory |
| Memory | Bounded context window with branching and optional compaction |

Next: [Tools](concepts/tools.md), [Turns](concepts/turns.md), [Agents](concepts/agents.md), or [Memory](concepts/memory.md).
