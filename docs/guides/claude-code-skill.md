# Claude Code Skill

This page contains a ready-to-use [Claude Code skill](https://docs.anthropic.com/en/docs/claude-code/skills) for pygents. **This skill is sufficient to teach any AI agent to write correct pygents code for the core patterns.** It covers all five abstractions with working examples, the value-routing table, all hook types and their call signatures, serialization, and common errors. Advanced features — agent and context branching, and pause/resume — are mentioned briefly but not fully explained; consult the full docs for those.

## Setup

Create the file `.claude/commands/pygents.md` at the root of your project and paste the content below. Then invoke it with `/pygents` in Claude Code.

````markdown
---
description: Reference for the pygents async agent orchestration library. Use when writing tools, turns, agents, context queues, context pools, or hooks with pygents.
---

# pygents

A lightweight async framework for structuring and running AI agents in Python. Requires Python 3.12+. Zero dependencies.

Five abstractions: **Tools** (how), **Turns** (what), **Agents** (orchestrate), **ContextQueue** (bounded context window — declare a typed parameter and the agent injects its instance automatically), **ContextPool** (keyed store for large tool outputs — tools return `ContextItem`s, the agent routes them in; same typed-parameter injection for reading).

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

# With tags — used to filter global hooks
@tool(tags=["io", "storage"])
async def write_db(data: dict) -> None: ...

# With hooks — attach after decoration via method decorators
@tool()
async def audited_tool(x: int) -> int: ...

@audited_tool.before_invoke
async def log_input(x: int) -> None: ...

@audited_tool.after_invoke
async def log_output(result: int) -> None: ...

@audited_tool.on_error
async def handle_err(exc: Exception) -> None: ...
```

Rules:
- Must be `async def` (sync raises `TypeError`)
- Fixed kwargs that don't match the signature (and no `**kwargs`) raise `TypeError`
- Duplicate names in `ToolRegistry` raise `ValueError`
- `ToolRegistry.get(name)` returns a tool; `.all()` returns all tools
- `tags` is a `list[str]` / `frozenset[str]` stored as `frozenset` on the instance; used to filter global `@hook` declarations — a global hook with `tags={"io"}` only fires for objects that share at least one tag (OR semantics; hooks with no `tags` fire for all objects). `tags=` works on `@tool()`, `Agent(...)`, `Turn(...)`, `ContextQueue(limit=...)`, and `ContextPool(...)` constructors.

Metadata: `my_tool.metadata.name`, `.description` (docstring), `.start_time`, `.end_time`, `.dict()`.

## Turns

A unit of work: which tool + arguments. Resolves the tool from `ToolRegistry` at construction.

```python
from pygents import Turn

# By name or callable reference
turn = Turn("fetch", kwargs={"url": "https://example.com"}, timeout=30)
turn = Turn(fetch, args=["https://example.com"])

# Execute directly (the agent does this automatically)
result = await turn.returning()          # coroutine tools
async for value in turn.yielding():      # async generator tools
    ...
```

Constructor: `Turn(tool, timeout=60, args=[], kwargs={}, tags=None)`. Attach hooks after construction via method decorators (`@turn.before_run`, `@turn.after_run`, `@turn.on_timeout`, `@turn.on_error`, `@turn.on_complete`) or `turn.hooks.append(h)`.

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
# Attach hooks after construction via method decorators or agent.hooks.append(h)
agent = Agent("worker", "Doubles numbers", [double, add])
agent.hooks.append(my_hook)

# Enqueue turns
await agent.put(Turn("double", kwargs={"x": 5}))

# Run — async generator yields (turn, value) pairs
async for turn, value in agent.run():
    print(value)
# Loop exits when queue is empty
```

Key behaviors:
- `put(turn)` validates the tool is in the agent's set
- After each turn: if output is a `Turn` → auto-enqueued; if output is a `ContextItem` with `id=None` → appended to `agent.context_queue`; if output is a `ContextItem` with `id` set → stored in `agent.context_pool`
- Coroutine tools yield one `(turn, value)`. Generator tools yield per value.
- Attributes are immutable while `run()` is active (`SafeExecutionError`)
- `run()` cannot be called again while already running (`SafeExecutionError`)

Inter-agent messaging:
```python
await alice.send("bob", Turn("work", kwargs={"x": 42}))
```
Looks up target in `AgentRegistry`. Raises `UnregisteredAgentError` if not found.

Branching:
```python
child = agent.branch("worker-2")                          # inherits tools, hooks, queue
child = agent.branch("worker-2", tools=[t1], hooks=[])   # override
```

Registry: `AgentRegistry.get(name)`, `.clear()`.

## ContextQueue

Bounded window backed by `collections.deque`. Evicts oldest when full. Use for context that should **always be present** when a tool runs — conversation history, system instructions, recent events. Declare `param: ContextQueue` in the tool signature — the agent provides its own instance automatically (context injection). No explicit wiring needed.

```python
from pygents import ContextQueue

cq = ContextQueue(limit=20)                 # limit must be >= 1
await cq.append(ContextItem(content="msg1"), ContextItem(content="msg2"))  # async, variadic; ContextItem only
await cq.clear()                            # remove all items (async, fires BEFORE_CLEAR/AFTER_CLEAR)
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
await pool.add(item)                       # ValueError if id=None or description=None
pool.get("doc-1")                          # lookup by id (sync)
await pool.remove("doc-1")                 # remove by id
await pool.clear()                         # remove all
pool.catalogue()                           # "- [id] description" string, one line per item
pool.items                                 # list of all ContextItems
```

**The agent owns writes.** When a tool returns a `ContextItem`, the agent stores it in `agent.context_pool` automatically. Tools only read from the pool — declare `param: ContextPool` and the agent injects its instance automatically (same injection as `ContextQueue`).

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

Async callbacks at five levels: Turn, Agent, Tool, ContextQueue, ContextPool.

Two attachment styles: **instance-scoped** (method decorators — fires only for that object, preferred) and **global** (`@hook(Type)` — fires for every matching object).

**Prefer instance-scoped hooks** — they fire only for that specific object, keeping scope explicit:

```python
@tool()
async def compute(x: int) -> int:
    return x * 2

@compute.before_invoke
async def validate(x: int) -> None:
    if x < 0:
        raise ValueError("x must be non-negative")
```

Instance hooks are available on every object type:

```python
# Tool
@my_tool.before_invoke
async def validate(x: int) -> None: ...

@my_tool.after_invoke
async def log_result(result: int) -> None: ...

@my_tool.on_error
async def handle_tool_err(exc: Exception) -> None: ...

# Async-gen tool only
@stream_tool.on_yield
async def per_value(value) -> None: ...

# Turn
turn = Turn("my_tool", kwargs={"x": 5})

@turn.before_run
async def before(turn) -> None: ...

@turn.on_error
async def on_turn_error(turn, exc) -> None: ...

@turn.on_complete
async def always(turn, stop_reason) -> None: ...

# Agent
agent = Agent("worker", "desc", [my_tool])

@agent.before_turn
async def before_each(agent) -> None: ...

@agent.after_turn
async def after_each(agent, turn) -> None: ...

# ContextQueue
cq = ContextQueue(limit=20)

@cq.before_append
async def before_append(queue, incoming, current) -> None: ...

@cq.on_evict
async def on_evict(queue, item) -> None: ...

# ContextPool
pool = ContextPool(limit=50)

@pool.after_add
async def after_add(pool, item) -> None: ...
```

**Global hooks** — `@hook(Type)` — fire for every instance of every matching object type. Use only for true cross-cutting concerns (process-wide logging, metrics):

```python
from pygents import hook, TurnHook, ToolHook

@hook(TurnHook.BEFORE_RUN)
async def log_all_turns(turn):
    print(f"Starting {turn.tool.metadata.name}")

# Tag-filtered: only fires for tools tagged "io"
@hook(ToolHook.AFTER_INVOKE, tags={"io"})
async def log_io(result) -> None:
    print(f"I/O result: {result}")
```

When both styles are active for the same event, instance hooks fire first, then global hooks. The same hook object is never called twice (deduplication).

Options: `lock=True` serializes concurrent runs (covers only the actual function call — lifecycle hooks run outside the lock); `**fixed_kwargs` merge into every call; `tags={...}` on global `@hook` filters by object tags (tools, agents, turns, context queues, context pools all support `tags=`); declare `param: ContextQueue | None` or `param: ContextPool | None` for context injection.

### All hook types and their arguments

**TurnHook:**
- `BEFORE_RUN(turn)` — before tool runs
- `AFTER_RUN(turn, output)` — after clean completion; `output` is the return value (or list of yielded values for generators)
- `ON_TIMEOUT(turn)` — turn timed out
- `ON_ERROR(turn, exception)` — non-timeout error
- `ON_COMPLETE(turn, stop_reason)` — always fires in finally block (clean, error, or timeout)

**AgentHook:**
- `BEFORE_TURN(agent)` — before consuming next turn
- `AFTER_TURN(agent, turn)` — after turn processed
- `ON_TURN_VALUE(agent, turn, value)` — after routing (context already updated), before yielding result
- `BEFORE_PUT(agent, turn)` — before enqueue
- `AFTER_PUT(agent, turn)` — after enqueue
- `ON_PAUSE(agent)` — run loop hit a paused gate
- `ON_RESUME(agent)` — gate released, before next turn

**ToolHook:**
- `BEFORE_INVOKE(*args, **kwargs)` — about to call
- `ON_YIELD(value)` — each yielded value (generators)
- `AFTER_INVOKE(result)` — coroutine tool: fires after the tool returns (not dispatched if tool raises), receives the return value
- `AFTER_INVOKE([values])` — async gen tool: fires after the generator is exhausted or the caller breaks early (not dispatched if it raises), receives the list of yielded values (partial on early break)
- `ON_ERROR(exc=exc)` — fires when the tool raises; `AFTER_INVOKE` does not fire in this case

**ContextQueueHook:**
- `BEFORE_APPEND(queue, incoming, current)` — queue instance, items being appended, snapshot before append
- `AFTER_APPEND(queue, appended_items, current)` — queue instance, items that were appended, snapshot after append
- `BEFORE_CLEAR(queue, items)` — queue instance, snapshot before clear
- `AFTER_CLEAR(queue)` — queue instance (now empty)
- `ON_EVICT(queue, item)` — queue instance, evicted ContextItem (fires once per eviction)

**ContextPoolHook:**
- `BEFORE_ADD(pool, item)` — before item inserted
- `AFTER_ADD(pool, item)` — after item inserted
- `BEFORE_REMOVE(pool, item)` — before item deleted
- `AFTER_REMOVE(pool, item)` — after item deleted
- `BEFORE_CLEAR(pool, snapshot)` — pool instance and dict copy of items taken before clear
- `AFTER_CLEAR(pool)` — after all items cleared
- `ON_EVICT(pool, item)` — oldest item about to be evicted when pool is at limit

## Tool-driven flow control

The core pattern: tools return `Turn` objects to control what runs next.

```python
@tool()
async def think(cq: ContextQueue, pool: ContextPool) -> Turn:
    if not pool:
        return Turn(respond)              # cq injected automatically
    return Turn(select_and_answer)        # cq and pool injected automatically

@tool()
async def respond(cq: ContextQueue) -> str:
    return "Final answer"
```

The agent auto-enqueues any `Turn` returned as output. Chain as many steps as needed. Because `ContextQueue` and `ContextPool` parameters are injected by the agent, returned `Turn`s rarely need explicit kwargs for context — only non-context arguments need to be passed.

## Serialization

`to_dict()` / `from_dict()` on `Turn`, `Agent`, `ContextQueue`, and `ContextPool`. Hooks serialize by name, resolved from `HookRegistry` on load.

```python
data = agent.to_dict()         # includes queue, current_turn, hooks, context_pool, context_queue, is_paused
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
