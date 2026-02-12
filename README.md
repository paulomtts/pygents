# pygents

A lightweight async framework for structuring and running AI agents in Python. Define tools, queue turns, stream results.

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
- **Per-tool locking** — opt-in serialization for shared state (lock is acquired inside the tool wrapper, so turn-level hooks run outside the tool lock)
- **Fixed kwargs** — decorator kwargs (e.g. `@tool(permission="admin")`) are merged into every invocation; call-time kwargs override
- **Hooks** — `@hook(hook_type, lock=..., **fixed_kwargs)` decorator; hooks stored as a list and selected by type; turn, agent, tool, and memory hooks; same fixed_kwargs and lock options as tools
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
      metadata={...},
      hooks=[...],
  )
  ```

  This keeps positional arguments explicit while reserving `metadata` purely for user-level annotations. `start_time`, `end_time`, `stop_reason`, and `output` are set by the framework during execution—they are not constructor parameters.

- **Agent serialization and current turn**: `Agent.to_dict()` includes a `current_turn` key when the agent is in the middle of `run()` (the turn being executed). That turn is already off the queue, so without it a snapshot would lose the in-flight work. `from_dict()` restores it; the next `run()` consumes the restored current turn first, then the queue. So you can save agent state at any time and get a faithful snapshot (config, queue, and current turn if any).

- **Tool and hook metadata timing**: `ToolMetadata` and `HookMetadata` include `start_time` and `end_time` (`datetime | None`). They are set on the metadata instance when the tool or hook runs (start at entry, end in a `finally`). So the same metadata object is updated each run; `dict()` includes ISO strings for serialization.

- **Hook protocol**: Registered hooks conform to a `Hook` protocol (like `Tool`), with `metadata` (`HookMetadata`: name, description, and run timing), `hook_type`, `fn`, and `lock`. The decorator sets `metadata` (from `__name__` and `__doc__`), `fn`, and `lock`; `HookRegistry.register()` sets `hook_type`. Raw async callables are typed as `Callable[..., Awaitable[None]]`.

- **Hook decorator**: `@hook(hook_type, lock=False, **fixed_kwargs)` mirrors the tool decorator: keyword arguments are merged into every invocation (call-time overrides), and `lock=True` uses an asyncio lock to serialize hook invocations. Pass a list of types (e.g. `@hook([TurnHook.BEFORE_RUN, AgentHook.AFTER_TURN])`) to reuse one hook for several events; multi-type hooks must accept `*args, **kwargs` since different types receive different arguments.

- **Tool lock in tool layer**: The tool lock is acquired inside the `@tool` wrapper, not in the turn. So the lock covers only the tool’s own execution (including its BEFORE_INVOKE / ON_YIELD / AFTER_INVOKE hooks). Turn-level hooks (BEFORE_RUN, AFTER_RUN, ON_TIMEOUT, ON_ERROR) run outside the tool lock; hooks that need serialization use their own `lock=True`.

- **Memory hooks**: `Memory` has no compact callback. It supports `MemoryHook.BEFORE_APPEND` and `MemoryHook.AFTER_APPEND` only. Hooks are stored as `list[Hook]` (like Agent/Turn), filtered by type when running. BEFORE_APPEND and AFTER_APPEND hooks receive `(items,)` — the current items as a list (read-only). Serialization uses the same by-type-by-name shape as Agent/Turn; `from_dict()` resolves names from `HookRegistry`.

## Docs

Full documentation: `uv run mkdocs serve`. MkDocs is an optional dependency—install with `pip install -e ".[docs]"` (or use `uv run` as above) so the library itself does not depend on it.
