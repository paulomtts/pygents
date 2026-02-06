# pygents

Async agent orchestration for Python. Define tools, queue turns, stream results.

## Install

```bash
pip install pygents
```

Requires Python 3.12+.

## Example

```python
import asyncio
from pygents import Agent, Turn, ToolType, tool

@tool()
async def greet(name: str) -> str:
    return f"Hello, {name}!"

@tool(type=ToolType.COMPLETION_CHECK)
async def done() -> bool:
    return True

async def main():
    agent = Agent("greeter", "Greets people", [greet, done])
    await agent.put(Turn("greet", kwargs={"name": "World"}))
    await agent.put(Turn("done"))

    async for turn, value in agent.run():
        print(value)  # "Hello, World!"

asyncio.run(main())
```

Tools are async functions. Turns say which tool to run and with what args. Agents process a queue of turns and stream results. The loop exits when a completion check returns `True`.

## Features

- **Streaming** — agents yield `(turn, value)` as results are produced
- **Completion checks** — tools that signal when to stop
- **Inter-agent messaging** — agents can send turns to each other
- **Dynamic arguments** — callable kwargs evaluated at runtime
- **Timeouts** — per-turn, default 60s
- **Per-tool locking** — opt-in serialization for shared state
- **Hooks** — async callbacks at turn, agent, and tool level
- **Serialization** — `to_dict()` / `from_dict()` for turns and agents

## Docs

Full documentation: `uv run mkdocs serve`
