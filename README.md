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
- **Subtools** — `@my_tool.subtool()` and `doc_tree()` for hierarchical tool docs (name, description, recursive subtools)
- **Serialization** — `to_dict()` / `from_dict()` for turns and agents

## Design Decisions

**Agent/Turn hook boundary** — `TurnHook` covers events fired by the Turn itself (`BEFORE_RUN`, `AFTER_RUN`, `ON_TIMEOUT`, `ON_ERROR`, `ON_COMPLETE`). `AgentHook` covers agent-loop events (`BEFORE_TURN`, `AFTER_TURN`, `ON_TURN_VALUE`, `BEFORE_PUT`, `AFTER_PUT`, `ON_PAUSE`, `ON_RESUME`). `ON_TURN_VALUE` stays on Agent because it fires after routing (agent logic). Turn-lifecycle hooks can be registered on an agent via `agent.turn_hooks` (or the `@agent.on_error` / `@agent.on_timeout` / `@agent.on_complete` decorators) and are automatically propagated to every turn the agent runs.

**Hook attachment style** — Hooks are attached via method decorators on the instance (`@agent.before_turn`, `@turn.on_complete`, `@my_tool.before_invoke`) rather than constructor parameters. This keeps the API surface explicit and enables IDE autocompletion of hook signatures.

**Subtools** — Subtools are normal registered tools (in `ToolRegistry`) that are also attached to a parent for hierarchical documentation. Use `@my_tool.subtool()` to register a subtool; use `doc_tree()` on any tool to get a recursive structure of name, description, and subtools (no runtime timing). Registry keys are scoped to the parent (e.g. `manage_users.create_user`) so different parents can have subtools with the same short name; use that scoped name in turns and lookups. Agents given a root tool accept turns for that tool and all its subtools.

**Tool call arguments** — Invoking a tool with extra positional or keyword arguments does not raise; only the parameters accepted by the tool function are forwarded. Missing required parameters still raise `TypeError` when the underlying function is called. This allows callers (e.g. agents or external systems) to pass a superset of arguments without errors.

## Docs

Full documentation: `uv run mkdocs serve`. MkDocs is an optional dependency—install with `pip install -e ".[docs]"` (or use `uv run` as above) so the library itself does not depend on it.
