# Hooks

Hooks are an advanced feature. If you're just starting out, read [Tools](tools.md), [Turns](turns.md), [Agents](agents.md), and [Context](context.md) first.

Hooks are async callables that run at specific points in the lifecycle of turns, agents, tools, and memory. Use them for logging, metrics, auditing, or injecting fixed context into every invocation.

## Defining hooks

There are two ways to attach a hook: the **global** `@hook(Type)` decorator, which fires for every matching object across the entire process, and **instance-scoped** method decorators (e.g. `@my_tool.before_invoke`, `@turn.before_run`), which fire only for the specific object they are attached to. See [Global vs instance-scoped hooks](#global-vs-instance-scoped-hooks) for a full comparison.

`@hook(type)` registers the hook globally and sets its type so the framework can select it by event.

```python
from pygents import hook, TurnHook

@hook(TurnHook.BEFORE_RUN)
async def log_start(turn):
    print(f"Starting {turn.tool.metadata.name}")

@hook(TurnHook.ON_ERROR)
async def on_error(turn, exception):
    print(f"Error: {exception}")
```

**Decorator parameters:**

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `type` | required | One or more of `TurnHook`, `AgentHook`, `ToolHook`, `ContextQueueHook`, `ContextPoolHook`. Stored on the hook for filtering. Pass a list (e.g. `@hook([TurnHook.BEFORE_RUN, AgentHook.AFTER_TURN])`) to register for several events. |
| `lock` | `False` | If `True`, concurrent runs of this hook are serialized via `asyncio.Lock`. |
| `**kwargs` | — | Fixed keyword arguments merged into every invocation. Call-time kwargs override these (with a warning). |

!!! warning "TypeError"
    Only async functions are accepted — sync `def` is not valid for hooks. Fixed kwargs that don't match the function's signature (and the function has no `**kwargs`) also raise `TypeError`.

!!! warning "ValueError"
    Passing an empty list as `type` raises `ValueError`.

Hooks are registered in `HookRegistry` at decoration time. The function name is the hook's identifier for lookup and serialization.

!!! warning "ValueError"
    Registering a *different* hook with a name already in use raises `ValueError`. Re-registering the same hook under the same name is allowed.

## Hook types

**Turn** — during turn execution (see [Turns](turns.md#hooks)):

| Hook | When | Args |
|------|------|------|
| `BEFORE_RUN` | Before tool runs (after lock acquired) | `(turn)` |
| `AFTER_RUN` | After successful completion | `(turn, output)` |
| `ON_TIMEOUT` | Turn timed out | `(turn)` |
| `ON_ERROR` | Tool or hook raised (non-timeout) | `(turn, exception)` |
| `ON_COMPLETE` | Always fires in finally block (clean, error, or timeout) | `(turn, stop_reason)` |

**Agent** — during the agent run loop (see [Agents](agents.md#hooks)):

| Hook | When | Args |
|------|------|------|
| `BEFORE_TURN` | Before consuming next turn from queue | `(agent)` |
| `AFTER_TURN` | After turn fully processed | `(agent, turn)` |
| `ON_TURN_VALUE` | After routing, before yielding each result | `(agent, turn, value)` |
| `BEFORE_PUT` | Before enqueueing a turn | `(agent, turn)` |
| `AFTER_PUT` | After enqueueing a turn | `(agent, turn)` |
| `ON_PAUSE` | When the run loop hits a paused gate | `(agent)` |
| `ON_RESUME` | After the gate is released and before the next turn | `(agent)` |

**Tool** — during tool invocation (see [Tool hooks](#tool-hooks)):

| Hook | When | Args |
|------|------|------|
| `BEFORE_INVOKE` | About to call the tool | `(**kwargs)` — same kwargs as the tool's own signature |
| `ON_YIELD` | Each yielded value (async generator tools only) | `(value)` |
| `AFTER_INVOKE` | After tool returns or finishes yielding | `(result)` — return value for coroutine tools; `list` of all yielded values for async-gen tools |

**ContextQueue** — during append and clear (see [ContextQueue](context.md#hooks)):

| Hook | When | Args |
|------|------|------|
| `BEFORE_APPEND` | Before new items are inserted | `(queue, incoming, current)` — queue instance, items being appended, snapshot of items before append |
| `AFTER_APPEND` | After new items have been added | `(queue, appended_items, current)` — queue instance, items that were appended, snapshot of items after append |
| `BEFORE_CLEAR` | Before items are cleared | `(queue, items)` — queue instance, snapshot of items before clear |
| `AFTER_CLEAR` | After items are cleared | `(queue)` — queue instance (now empty) |
| `ON_EVICT` | When an item is evicted to make room | `(queue, item)` — queue instance, evicted `ContextItem` |

**ContextPool** — during pool mutation (see [Context Pool](context.md#hooks_1)):

| Hook | When | Args |
|------|------|------|
| `BEFORE_ADD` | Before item inserted (after eviction if any) | `(pool, item)` |
| `AFTER_ADD` | After item inserted | `(pool, item)` |
| `BEFORE_REMOVE` | Before item deleted | `(pool, item)` |
| `AFTER_REMOVE` | After item deleted | `(pool, item)` |
| `BEFORE_CLEAR` | Before all items cleared | `(pool, snapshot)` — dict copy of items taken before clear |
| `AFTER_CLEAR` | After all items cleared | `(pool)` |

## Global vs instance-scoped hooks

There are two ways to attach a hook, and they have very different reach.

### Global hooks — `@hook(Type)`

A hook decorated with `@hook(Type)` is **global**: it fires for every object of every matching type, across the entire process. It is stored in `HookRegistry._global_hooks` and is automatically included when any turn, agent, tool, queue, or pool dispatches that event.

```python
from pygents import hook, TurnHook

@hook(TurnHook.BEFORE_RUN)
async def log_all_turns(turn):
    print(f"Starting {turn.tool.metadata.name}")
```

`log_all_turns` fires before every turn, regardless of which tool it runs or which agent owns it. Use global hooks for cross-cutting concerns: logging, metrics, auditing.

### Instance-scoped hooks — method decorators

A hook attached via a method decorator (`@my_tool.before_invoke`, `@turn.before_run`, `@agent.before_turn`, `@cq.before_append`, `@pool.before_add`, etc.) fires **only for that specific object**. It is stored in the object's own `.hooks` list and is invisible to other instances.

```python
from pygents import tool

@tool()
async def my_tool(x: int) -> int:
    return x * 2

@my_tool.before_invoke
async def validate_input(x: int) -> None:
    if x < 0:
        raise ValueError("x must be non-negative")
```

`validate_input` fires only when `my_tool` is invoked. A different tool in the same process is unaffected.

The same principle applies to turns, agents, context queues, and context pools:

```python
turn = Turn("my_tool", kwargs={"x": 5})

@turn.on_error
async def handle_error(turn, exc):
    print(f"This turn failed: {exc}")

agent = Agent("worker", "desc", [my_tool])

@agent.before_turn
async def before_each_turn(agent):
    print("agent worker starting a turn")
```

`handle_error` fires only if this particular turn fails. `before_each_turn` fires only for `agent`, not for any other agent instance.

### Both can coexist

Global and instance hooks for the same event are merged at dispatch time and fire together. Instance hooks run first (in list order), then any global hooks that are not already in the instance list. If the exact same hook object appears in both, it fires only once (deduplication prevents double-firing).

```python
@hook(ToolHook.BEFORE_INVOKE)
async def global_before(**kwargs):
    print("fires for every tool")

@my_tool.before_invoke
async def instance_before(**kwargs):
    print("fires only for my_tool")

# When my_tool is invoked, both hooks fire:
# 1. instance_before  (instance list)
# 2. global_before    (global list, not a duplicate)
```

### Where hooks attach

| Object | Instance-scoped | Global equivalent |
|--------|----------------|-------------------|
| Tool | `@my_tool.before_invoke`, `.on_yield`, `.after_invoke` | `@hook(ToolHook.*)` |
| Turn | `@turn.before_run`, `.after_run`, `.on_timeout`, `.on_error`, `.on_complete` | `@hook(TurnHook.*)` |
| Agent | `@agent.before_turn`, `.after_turn`, `.on_turn_value`, `.before_put`, `.after_put`, `.on_pause`, `.on_resume` | `@hook(AgentHook.*)` |
| Agent (turn-scoped) | `@agent.on_error`, `.on_timeout`, `.on_complete` → stored in `agent.turn_hooks`, propagated to each turn | `@hook(TurnHook.*)` |
| ContextQueue | `@cq.before_append`, `.after_append`, `.before_clear`, `.after_clear`, `.on_evict` | `@hook(ContextQueueHook.*)` |
| ContextPool | `@pool.before_add`, `.after_add`, `.before_remove`, `.after_remove`, `.before_clear`, `.after_clear`, `.on_evict` | `@hook(ContextPoolHook.*)` |

All hooks whose type matches the event are invoked sequentially, in the order they appear in the merged list. If a hook raises, execution stops and the exception propagates; later hooks in the same event are not called.

## Tool hooks

Attach lifecycle hooks to a tool using **method decorators** after the tool is defined. The decorator syntax registers the hook, assigns its type, and stores it on the tool instance.

```python
from pygents import tool

@tool()
async def my_tool(x: int, y: str) -> str:
    return f"{x}-{y}"

@my_tool.before_invoke
async def log_before(x: int, y: str) -> None:
    print(f"calling with x={x}, y={y}")

@my_tool.after_invoke
async def log_after(result) -> None:
    print(f"result: {result}")
```

You can also call the method directly with a plain async function (without the `@` syntax):

```python
async def validate(x: int, y: str) -> None:
    assert x > 0

my_tool.before_invoke(validate)
```

The three hook points available on tools:

| Method | Hook type | Callback receives |
|--------|-----------|-------------------|
| `.before_invoke` | `BEFORE_INVOKE` | `**kwargs` — the tool's own keyword arguments (plus context-injected deps if declared) |
| `.on_yield` | `ON_YIELD` | `(value,)` — each yielded value; only fires for async-generator tools |
| `.after_invoke` | `AFTER_INVOKE` | `(result,)` — return value for coroutine tools, or `list` of all yielded values for async-gen tools |

`AFTER_INVOKE` does **not** fire if the tool raises an exception.

To share a hook across multiple tools, use stacked decorators:

```python
@tool()
async def tool_a(x: int) -> int:
    return x

@tool()
async def tool_b(x: int) -> int:
    return x

@tool_a.after_invoke
@tool_b.before_invoke
async def shared_hook(x):
    print(x)
```

## Multi-type hooks

To reuse the same hook for multiple event types, pass a list of types:

```python
@hook([AgentHook.BEFORE_TURN, AgentHook.AFTER_TURN])
async def log_turn_events(*args, **kwargs):
    print(f"Event: {args}")
```

Multi-type hooks **must** accept `*args, **kwargs` because different hook types receive different arguments (e.g. `BEFORE_TURN` gets `(agent,)`, `AFTER_TURN` gets `(agent, turn)`). You can inspect `args` to distinguish the event. For single-type hooks, the decorator provides **type-safe overloaded signatures** so your IDE can infer the exact callback shape (e.g. `(Turn) -> Awaitable[None]` for `BEFORE_RUN`).

All hook types are members of the `HookType` union: `TurnHook | AgentHook | ToolHook | ContextQueueHook | ContextPoolHook`.

## Fixed kwargs

Pass keyword arguments to the decorator to inject the same values into every call. Call-site kwargs override with a warning.

```python
@hook(TurnHook.AFTER_RUN, env="production")
async def report(turn, output, env):
    send_metric(turn.tool.metadata.name, env=env)
```

Use this for environment labels, log handles, or other context that is constant for the hook's lifetime.

## Context injection

Hooks can declare `ContextQueue` or `ContextPool` parameters (including optional `ContextQueue | None`) and receive the agent's active instances automatically — the same injection mechanism used by tools. Injection only occurs when the context vars are live (i.e. during turn execution); hooks that fire outside a turn (e.g. `AgentHook.BEFORE_TURN`, `AgentHook.AFTER_TURN`) will receive `None` for optional parameters and no injection for required ones.

```python
from pygents import hook, ToolHook
from pygents.context import ContextQueue

@hook(ToolHook.AFTER_INVOKE)
async def log_result(value, memory: ContextQueue | None = None):
    if memory is not None:
        print(f"Result: {value}, context items: {len(memory)}")
```

## Locking

Set `lock=True` to serialize concurrent executions of the same hook with an `asyncio.Lock`. Useful when the hook writes to shared state (e.g. a single log file or metrics buffer).

```python
@hook(TurnHook.AFTER_RUN, lock=True)
async def write_log(turn, output):
    async with open("run.log", "a") as f:
        await f.write(f"{turn.tool.metadata.name}\n")
```

## Metadata and timing

Each hook has a `metadata` attribute: `HookMetadata(name, description, start_time, end_time)`. The decorator sets `name` and `description` (docstring). `start_time` and `end_time` are set around each invocation.

```python
@hook(TurnHook.BEFORE_RUN)
async def timed(turn):
    """Logs turn start."""
    pass

# after invocation
timed.metadata.start_time  # datetime when wrapper entered
timed.metadata.end_time    # datetime when wrapper exited
timed.metadata.dict()      # ISO strings for serialization
```

## Registry

Hooks register globally when decorated. Look up by name when deserializing or when you need the same instance elsewhere.

```python
from pygents import HookRegistry

my_hook = HookRegistry.get("log_start")
HookRegistry.clear()  # empty the registry (useful in tests)
```

!!! warning "UnregisteredHookError"
    `HookRegistry.get(name)` raises `UnregisteredHookError` if no hook is registered with that name.

`get_by_type` is used internally: given a list of hooks (e.g. `turn.hooks`), it returns all hooks whose type matches, in the order they appear in the list. All matching hooks are called sequentially. You typically don't call it directly; you attach hooks to turns, agents, tools, or memory and the framework invokes them at each event.

## Protocol

The `Hook` protocol defines the shape every decorated hook conforms to:

```python
class Hook(Protocol):
    metadata: HookMetadata          # name, description, start_time, end_time
    type: HookType | tuple[HookType, ...] | None
    fn: Callable[..., Awaitable[None]]
    lock: asyncio.Lock | None
```

## Errors

| Exception | When |
|-----------|------|
| `TypeError` | Decorating a sync function, or fixed kwargs not in signature |
| `ValueError` | Empty type list, or duplicate hook name in `HookRegistry` |
| `UnregisteredHookError` | `HookRegistry.get()` with unknown name |
