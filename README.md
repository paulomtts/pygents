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
from pygents import Agent, Turn, tool

@tool()
async def greet(name: str) -> str:
    return f"Hello, {name}!"

async def main():
    agent = Agent("greeter", "Greets people", [greet])
    # Use kwargs:
    await agent.put(Turn("greet", kwargs={"name": "World"}))
    # Or positional args:
    await agent.put(Turn("greet", args=["World"]))

    async for turn, value in agent.run():
        print(value)  # "Hello, World!"

asyncio.run(main())
```

Tools are async functions. Turns say which tool to run and with what args. Agents process a queue of turns and stream results. The loop exits when the queue is empty.

## Features

- **Streaming** — agents yield `(turn, value)` as results are produced
- **Inter-agent messaging** — agents can send turns to each other
- **Dynamic arguments** — callable positional args and kwargs evaluated at runtime
- **Timeouts** — per-turn, default 60s
- **Per-tool locking** — opt-in serialization for shared state
- **Fixed kwargs** — decorator kwargs (e.g. `@tool(permission="admin")`) are merged into every invocation; call-time kwargs override
- **Hooks** — async callbacks at turn, agent, and tool level
- **Serialization** — `to_dict()` / `from_dict()` for turns and agents

## Design decisions

- **Turn identity**: `Turn` instances no longer have a built-in `uuid`. If you need identifiers, store them yourself in `metadata` or wrap `Turn` in a higher-level domain object.
- **Turn arguments**: `Turn.__init__` takes `args` before `kwargs`, and `metadata` is the final parameter:

  ```python
  Turn(
      "tool_name",
      args=[...],
      kwargs={...},
      timeout=...,
      start_time=...,
      end_time=...,
      stop_reason=...,
      metadata={...},
  )
  ```

  This keeps positional arguments explicit while reserving `metadata` purely for user-level annotations.

## Docs

Full documentation: `uv run mkdocs serve`. MkDocs is an optional dependency—install with `pip install -e ".[docs]"` (or use `uv run` as above) so the library itself does not depend on it.
