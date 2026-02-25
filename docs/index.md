# pygents

A lightweight async framework for structuring and running AI agents in Python. pygents is a structural framework, not a batteries-included toolkit — it gives you the primitives for organizing and running agents (queues, turns, hooks, streaming) but leaves the concrete implementations to you. There are no built-in LLM clients, prompt templates, or retrieval pipelines. You bring your own. `ContextQueue`, for example, provides a bounded, branchable window you can subclass or compose into working memory, semantic memory, episodic memory, or whatever your agent needs — pygents only manages the container and its lifecycle.

This means zero external dependencies and full control over every layer of your agent's behavior.

Five abstractions:

- **Tools** define _how_ — async functions decorated with `@tool`
- **Turns** define _what_ — which tool to run with what arguments
- **Agents** _orchestrate_ — process a queue of turns and stream results
- **ContextQueue** provides _working context_ — bounded, branchable window of raw items
- **ContextPool** accumulates _tool outputs_ — keyed, bounded collection of `ContextItem` objects

For the design rationale behind these abstractions, see [Structural Principles for Agent Systems](whitepaper.md).

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
    # "fetch" is the tool's registered name; Turn(fetch, ...) also works
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
await alice.send("bob", Turn("work_tool", kwargs={"x": 42}))
```

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

## Capabilities

| Feature | Description |
|---------|-------------|
| Streaming | Agents yield results as produced via `async for turn, value in agent.run()` |
| Inter-agent messaging | `agent.send(name, turn)` enqueues work on another agent |
| Dynamic arguments | Callable positional args and kwargs evaluated at invocation time |
| Timeouts | Per-turn timeout (default 60s), raises `TurnTimeoutError` |
| Per-tool locking | `@tool(lock=True)` serializes concurrent runs |
| Pause / resume | `agent.pause()` / `agent.resume()` gate the run loop between turns |
| Hooks | Async callbacks at turn, agent, tool, context queue, and context pool level |
| Serialization | `to_dict()` / `from_dict()` for turns, agents, context queues, and context pools |
| ContextQueue | Bounded context window with branching |
| Context injection | Typed `ContextQueue` / `ContextPool` parameters are automatically provided by the running agent |

## Registries

Three global registries manage named lookups. Registration is automatic (at decoration time for tools/hooks, at construction time for agents).

| Registry | `get` | `all` | `clear` | `get_by_type` |
|----------|-------|-------|---------|---------------|
| `ToolRegistry` | by name | all tools | — | — |
| `AgentRegistry` | by name | — | empties | — |
| `HookRegistry` | by name | — | empties | first match by type |

## Public API

Everything importable from `pygents`:

| Category | Symbols |
|----------|---------|
| Core classes | `Agent`, `Turn`, `ContextQueue`, `ContextPool` |
| Context | `ContextItem` (from `pygents.context`) |
| Decorators | `@tool`, `@hook` |
| Enums | `StopReason`, `TurnHook`, `AgentHook`, `ToolHook`, `ContextQueueHook`, `ContextPoolHook` |
| Protocols | `Tool`, `Hook` |
| Metadata | `ToolMetadata`, `HookMetadata` |
| Registries | `ToolRegistry`, `AgentRegistry`, `HookRegistry` |
| Exceptions | `SafeExecutionError`, `WrongRunMethodError`, `TurnTimeoutError`, `UnregisteredToolError`, `UnregisteredAgentError`, `UnregisteredHookError` |

Next: [Tools](concepts/tools.md), [Turns](concepts/turns.md), [Agents](concepts/agents.md), [Context](concepts/context.md), [Hooks](concepts/hooks.md).

Guides: [Building a Research Assistant](guides/research-assistant.md), [Claude Code Skill](guides/claude-code-skill.md).
