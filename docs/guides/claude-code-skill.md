# Claude Code Skill

This page contains a ready-to-use [Claude Code skill](https://docs.anthropic.com/en/docs/claude-code/skills) that teaches Claude how to build with pygents. Copy it into your project so Claude can help you write tools, agents, and hooks without needing to re-read the docs every time.

## Setup

Create the file `.claude/commands/pygents.md` at the root of your project and paste the content below. Then invoke it with `/pygents` in Claude Code.

````markdown
---
description: Reference for the pygents async agent orchestration library. Use when writing tools, turns, agents, memory, or hooks with pygents.
---

# pygents

A lightweight async framework for structuring and running AI agents in Python. Requires Python 3.12+. Zero dependencies.

Four abstractions: **Tools** (how), **Turns** (what), **Agents** (orchestrate), **Memory** (context).

## Tools

Async functions decorated with `@tool`. Register globally by function name.

```python
from pygents import tool

# Coroutine tool — single return value
@tool()
async def fetch(url: str) -> str:
    return f"<content from {url}>"

# Async generator tool — streams values
@tool()
async def stream(n: int):
    for i in range(n):
        yield i

# With locking — serializes concurrent runs via asyncio.Lock
@tool(lock=True)
async def write_db(data: dict) -> None: ...

# With fixed kwargs — merged into every call, call-time overrides
@tool(api_key=lambda: get_config()["key"])
async def call_api(endpoint: str, api_key: str) -> str: ...

# With hooks — fire on every invocation
@tool(hooks=[my_before_hook, my_after_hook])
async def audited_tool(x: int) -> int: ...
```

Rules:
- Must be `async def` (sync raises `TypeError`)
- Fixed kwargs that don't match the signature (and no `**kwargs`) raise `TypeError`
- Duplicate names in `ToolRegistry` raise `ValueError`
- `ToolRegistry.get(name)` returns a tool; `.all()` returns all tools

Metadata: `my_tool.metadata.name`, `.description` (docstring), `.start_time`, `.end_time`, `.dict()`.

## Turns

A unit of work: which tool + arguments. Resolves the tool from `ToolRegistry` at construction.

```python
from pygents import Turn

# By name or callable reference
turn = Turn("fetch", kwargs={"url": "https://example.com"}, timeout=30)
turn = Turn(fetch, args=["https://example.com"])

# With hooks
turn = Turn("fetch", kwargs={"url": "..."}, hooks=[my_turn_hook])

# Execute directly (the agent does this automatically)
result = await turn.returning()          # coroutine tools
async for value in turn.yielding():      # async generator tools
    ...
```

Constructor: `Turn(tool, args=[], kwargs={}, timeout=60, metadata={}, hooks=[])`.

After execution the framework sets: `output` (value or list of yielded values for generators), `start_time`, `end_time`, `stop_reason` (`StopReason.COMPLETED | TIMEOUT | ERROR | CANCELLED`).

Dynamic arguments — callables in `args`/`kwargs` are evaluated at run time:
```python
Turn("fetch", kwargs={"token": lambda: get_fresh_token()})
```

Immutability: `tool`, `args`, `kwargs`, `timeout` cannot change while running (`SafeExecutionError`). `metadata` can.

## Agents

Orchestrator: owns a queue of turns and a set of tools. Auto-registers with `AgentRegistry`.

```python
from pygents import Agent, Turn

agent = Agent("worker", "Doubles numbers", [double, add])

# Enqueue turns
await agent.put(Turn("double", kwargs={"x": 5}))

# Run — async generator yields (turn, value) pairs
async for turn, value in agent.run():
    print(value)
# Loop exits when queue is empty
```

Key behaviors:
- `put(turn)` validates the tool is in the agent's set
- After each turn, if `turn.output` is a `Turn`, the agent auto-enqueues it (tool-driven flow control)
- Coroutine tools yield one `(turn, value)`. Generator tools yield per value.
- Attributes are immutable while `run()` is active (`SafeExecutionError`)
- `run()` cannot be called again while already running (`SafeExecutionError`)

Inter-agent messaging:
```python
await alice.send_turn("bob", Turn("work", kwargs={"x": 42}))
```
Looks up target in `AgentRegistry`. Raises `UnregisteredAgentError` if not found.

Registry: `AgentRegistry.get(name)`, `.clear()`.

## Memory

Bounded window backed by `collections.deque`. Evicts oldest when full.

```python
from pygents import Memory

mem = Memory(limit=20)                    # limit must be >= 1
await mem.append("msg1", "msg2")          # async, variadic
mem.clear()                               # remove all items
mem.items                                 # list copy
len(mem)                                  # count
bool(mem)                                 # False when empty
```

Branching — child gets a snapshot, then diverges independently:
```python
child = mem.branch()                      # inherits limit and hooks
child = mem.branch(limit=5)               # smaller window
child = mem.branch(hooks=[])              # no hooks
```

## Hooks

Async callbacks at four levels: Turn, Agent, Tool, Memory. Decorated with `@hook(type)`.

```python
from pygents import hook, TurnHook, AgentHook, ToolHook, MemoryHook

@hook(TurnHook.BEFORE_RUN)
async def log_start(turn):
    print(f"Starting {turn.tool.metadata.name}")

@hook(AgentHook.ON_TURN_ERROR)
async def on_error(agent, turn, exception):
    print(f"Error in {agent.name}: {exception}")

@hook(ToolHook.AFTER_INVOKE)
async def log_result(value):
    print(f"Result: {value}")

@hook(MemoryHook.AFTER_APPEND)
async def log_memory(items):
    print(f"Memory now has {len(items)} items")
```

Attach hooks by appending to `.hooks` lists or passing in constructors:
- Turns: `Turn(..., hooks=[h])` or `turn.hooks.append(h)`
- Agents: `agent.hooks.append(h)`
- Tools: `@tool(hooks=[h])`
- Memory: `Memory(limit=N, hooks=[h])` or `mem.branch(hooks=[h])`

Hook decorator options: `@hook(type, lock=True, **fixed_kwargs)`.
- `lock=True` serializes concurrent runs via `asyncio.Lock`
- Fixed kwargs merge into every call (call-time overrides with warning)

Multi-type hooks — one hook for several events (must accept `*args, **kwargs`):
```python
@hook([AgentHook.BEFORE_TURN, AgentHook.AFTER_TURN])
async def log_events(*args, **kwargs): ...
```

### All hook types and their arguments

**TurnHook:**
- `BEFORE_RUN(turn)` — before tool runs
- `AFTER_RUN(turn)` — after success
- `ON_TIMEOUT(turn)` — turn timed out
- `ON_ERROR(turn, exception)` — non-timeout error
- `ON_VALUE(turn, value)` — each yielded value (streaming)

**AgentHook:**
- `BEFORE_TURN(agent)` — before consuming next turn
- `AFTER_TURN(agent, turn)` — after turn processed
- `ON_TURN_VALUE(agent, turn, value)` — before yielding result
- `ON_TURN_ERROR(agent, turn, exception)` — turn error
- `ON_TURN_TIMEOUT(agent, turn)` — turn timeout
- `BEFORE_PUT(agent, turn)` — before enqueue
- `AFTER_PUT(agent, turn)` — after enqueue

**ToolHook:**
- `BEFORE_INVOKE(*args, **kwargs)` — about to call
- `ON_YIELD(value)` — each yielded value (generators)
- `AFTER_INVOKE(value)` — after return/last yield

**MemoryHook:**
- `BEFORE_APPEND(items)` — current items (read-only)
- `AFTER_APPEND(items)` — current items after append

## Tool-driven flow control

The core pattern: tools return `Turn` objects to control what runs next.

```python
@tool()
async def think(memory: Memory) -> Turn:
    # Decide next step, then queue it
    if should_respond:
        return Turn(respond, kwargs={"memory": memory})
    return Turn(gather_info, kwargs={"memory": memory})

@tool()
async def respond(memory: Memory) -> str:
    return "Final answer"
```

The agent auto-enqueues any `Turn` returned as output. Chain as many steps as needed.

## Serialization

`to_dict()` / `from_dict()` on Turn, Agent, and Memory. Hooks serialize by name, resolved from `HookRegistry` on load.

```python
data = agent.to_dict()        # includes queue, current_turn, hooks
agent = Agent.from_dict(data)  # rebuilds from ToolRegistry, AgentRegistry, HookRegistry

data = turn.to_dict()
turn = Turn.from_dict(data)

data = mem.to_dict()
mem = Memory.from_dict(data)
```

## Common errors

- `TypeError` — sync function passed to `@tool`/`@hook`, or invalid fixed kwargs
- `ValueError` — duplicate registry name, empty hook type list, tool not in agent set
- `SafeExecutionError` — mutating immutable attrs or re-entering `run()`/`returning()`/`yielding()` while running
- `WrongRunMethodError` — `returning()` on generator or `yielding()` on coroutine
- `TurnTimeoutError` — turn exceeds timeout (default 60s)
- `UnregisteredToolError` / `UnregisteredAgentError` / `UnregisteredHookError` — name not in registry
````
