# Claude Code Skill

This page contains a ready-to-use [Claude Code skill](https://docs.anthropic.com/en/docs/claude-code/skills) that teaches Claude how to build with pygents. Copy it into your project so Claude can help you write tools, agents, and hooks without needing to re-read the docs every time.

## Setup

Create the file `.claude/commands/pygents.md` at the root of your project and paste the content below. Then invoke it with `/pygents` in Claude Code.

````markdown
---
description: Reference for the pygents async agent orchestration library. Use when writing tools, turns, agents, context queues, context pools, or hooks with pygents.
---

# pygents

A lightweight async framework for structuring and running AI agents in Python. Requires Python 3.12+. Zero dependencies.

Five abstractions: **Tools** (how), **Turns** (what), **Agents** (orchestrate), **ContextQueue** (memory-like context always passed to tools), **ContextPool** (large tool outputs retrieved selectively).

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

Constructor: `Turn(tool, timeout=60, args=[], kwargs={}, hooks=[])`.

After execution the framework sets: `output` (value or list of yielded values for generators), `metadata.start_time`, `metadata.end_time`, `metadata.stop_reason` (`StopReason.COMPLETED | TIMEOUT | ERROR | CANCELLED`).

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

# Optional: pre-configured context pool
agent = Agent("worker", "Doubles numbers", [double, add], context_pool=ContextPool(limit=50))

# Enqueue turns
await agent.put(Turn("double", kwargs={"x": 5}))

# Run — async generator yields (turn, value) pairs
async for turn, value in agent.run():
    print(value)
# Loop exits when queue is empty
```

Key behaviors:
- `put(turn)` validates the tool is in the agent's set
- After each turn: if output is a `Turn` → auto-enqueued; if output is a `ContextItem` → stored in `agent.context_pool`
- Coroutine tools yield one `(turn, value)`. Generator tools yield per value.
- Attributes are immutable while `run()` is active (`SafeExecutionError`)
- `run()` cannot be called again while already running (`SafeExecutionError`)

Inter-agent messaging:
```python
await alice.send_turn("bob", Turn("work", kwargs={"x": 42}))
```
Looks up target in `AgentRegistry`. Raises `UnregisteredAgentError` if not found.

Branching:
```python
child = agent.branch("worker-2")                          # inherits tools, hooks, queue
child = agent.branch("worker-2", tools=[t1], hooks=[])   # override
```

Registry: `AgentRegistry.get(name)`, `.clear()`.

## ContextQueue

Bounded window backed by `collections.deque`. Evicts oldest when full. Use for context that should **always be present** when a tool runs — conversation history, system instructions, recent events. Pass it explicitly as a tool argument; every tool that needs it receives the same instance.

```python
from pygents import ContextQueue

cq = ContextQueue(limit=20)                 # limit must be >= 1
await cq.append("msg1", "msg2")            # async, variadic
cq.clear()                                  # remove all items
cq.items                                    # list copy
len(cq)                                     # count
bool(cq)                                    # False when empty
```

Branching — child gets a snapshot, then diverges independently:
```python
child = cq.branch()                         # inherits limit and hooks
child = cq.branch(limit=5)                  # smaller window
child = cq.branch(hooks=[])                 # no hooks
```

Serialization:
```python
data = cq.to_dict()
cq = ContextQueue.from_dict(data)
```

## ContextPool

Keyed, bounded store for `ContextItem` objects. Use for **potentially large tool outputs** — documents, records, fetched data — that accumulate over a session and are retrieved selectively. Dumping everything into a prompt is expensive; instead, tools read descriptions to decide what's relevant, then pull only that content. Items are retrieved by `id`, not by position.

```python
from pygents.context import ContextItem, ContextPool

pool = ContextPool(limit=50)               # limit=None for unbounded
item = ContextItem(id="doc-1", description="Q3 earnings — revenue, margins", content={"text": "..."})
await pool.add(item)
pool.get("doc-1")                          # lookup by id (sync)
await pool.remove("doc-1")                 # remove by id
await pool.clear()                         # remove all
pool.catalogue()                           # "- [id] description" string, one line per item
pool.items                                 # list of all ContextItems
```

**The agent owns writes.** When a tool returns a `ContextItem`, the agent stores it in `agent.context_pool` automatically. Tools only read from the pool.

```python
@tool()
async def fetch_doc(doc_id: str) -> ContextItem:
    return ContextItem(id=doc_id, description="...", content=fetch(doc_id))

@tool()
async def answer(pool: ContextPool, question: str) -> str:
    catalogue = pool.catalogue()           # descriptions only — cheap
    selected_id = pick_relevant(catalogue, question)
    content = pool.get(selected_id).content
    return generate_answer(question, content)
```

Branching:
```python
child = pool.branch()           # inherits limit, items snapshot, hooks
child = pool.branch(limit=10)   # override limit
```

## Hooks

Async callbacks at five levels: Turn, Agent, Tool, ContextQueue, ContextPool. Decorated with `@hook(type)`.

```python
from pygents import hook, TurnHook, AgentHook, ToolHook, ContextQueueHook, ContextPoolHook

@hook(TurnHook.BEFORE_RUN)
async def log_start(turn):
    print(f"Starting {turn.tool.metadata.name}")

@hook(AgentHook.ON_TURN_ERROR)
async def on_error(agent, turn, exception):
    print(f"Error in {agent.name}: {exception}")

@hook(ToolHook.AFTER_INVOKE)
async def log_result(value):
    print(f"Result: {value}")

@hook(ContextQueueHook.AFTER_APPEND)
async def log_cq(items):
    print(f"ContextQueue now has {len(items)} items")

@hook(ContextPoolHook.AFTER_ADD)
async def log_pool(pool, item):
    print(f"Pool gained {item.id!r}: {item.description}")
```

Attach hooks:
- Turns: `Turn(..., hooks=[h])` or `turn._hooks.append(h)`
- Agents: `agent.hooks.append(h)`
- Tools: `@tool(hooks=[h])`
- ContextQueue: `ContextQueue(limit=N, hooks=[h])` or `cq.branch(hooks=[h])`
- ContextPool: `ContextPool(hooks=[h])` or via `Agent(..., context_pool=ContextPool(hooks=[h]))`

Hook decorator options: `@hook(type, lock=True, **fixed_kwargs)`.
- `lock=True` serializes concurrent runs via `asyncio.Lock`
- Fixed kwargs merge into every call (call-time overrides)

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

**ContextQueueHook:**
- `BEFORE_APPEND(items)` — current items (read-only)
- `AFTER_APPEND(items)` — current items after append

**ContextPoolHook:**
- `BEFORE_ADD(pool, item)` — before item inserted
- `AFTER_ADD(pool, item)` — after item inserted
- `BEFORE_REMOVE(pool, item)` — before item deleted
- `AFTER_REMOVE(pool, item)` — after item deleted
- `BEFORE_CLEAR(pool)` — before all items cleared
- `AFTER_CLEAR(pool)` — after all items cleared

## Tool-driven flow control

The core pattern: tools return `Turn` objects to control what runs next.

```python
@tool()
async def think(cq: ContextQueue, pool: ContextPool) -> Turn:
    if not pool:
        return Turn(respond, kwargs={"cq": cq})
    return Turn(select_and_answer, kwargs={"cq": cq, "pool": pool})

@tool()
async def respond(cq: ContextQueue) -> str:
    return "Final answer"
```

The agent auto-enqueues any `Turn` returned as output. Chain as many steps as needed.

## Serialization

`to_dict()` / `from_dict()` on `Turn`, `Agent`, `ContextQueue`, and `ContextPool`. Hooks serialize by name, resolved from `HookRegistry` on load.

```python
data = agent.to_dict()         # includes queue, current_turn, hooks, context_pool
agent = Agent.from_dict(data)  # rebuilds from ToolRegistry, AgentRegistry, HookRegistry

data = turn.to_dict()
turn = Turn.from_dict(data)

data = cq.to_dict()
cq = ContextQueue.from_dict(data)
```

## Common errors

- `TypeError` — sync function passed to `@tool`/`@hook`, or invalid fixed kwargs
- `ValueError` — duplicate registry name, empty hook type list, tool not in agent set
- `SafeExecutionError` — mutating immutable attrs or re-entering `run()`/`returning()`/`yielding()` while running
- `WrongRunMethodError` — `returning()` on generator or `yielding()` on coroutine
- `TurnTimeoutError` — turn exceeds timeout (default 60s)
- `UnregisteredToolError` / `UnregisteredAgentError` / `UnregisteredHookError` — name not in registry
````
