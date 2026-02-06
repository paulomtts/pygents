# Quick start

Install and run the project (e.g. with `uv run`). Then:

```python
import asyncio
from app import Agent, ToolRegistry, ToolType, tool, Turn

@tool(type=ToolType.ACTION)
async def greet(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"

@tool(type=ToolType.COMPLETION_CHECK)
async def is_done() -> bool:
    """Stop after one greeting."""
    return True

async def main():
    agent = Agent("runner", "Simple runner", [greet, is_done])
    await agent.put(Turn("greet", kwargs={"name": "World"}))
    await agent.put(Turn("is_done"))

    async for turn, value in agent.run():
        print(value)   # Hello, World! then loop exits

asyncio.run(main())
```

- Tools are registered when decorated. The agent is given the same tool instances and owns a queue.
- `put()` enqueues turns; `run()` pops, runs, and yields `(turn, value)`. The completion-check tool returning `True` ends the loop.

For streaming tools, use async generators and run turns with `yielding()` (or let the agent call it for you). For more structure, see [Concepts](concepts/tools.md).
