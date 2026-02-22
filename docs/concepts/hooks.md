# Hooks

Hooks are an advanced feature. If you're just starting out, read [Tools](tools.md), [Turns](turns.md), [Agents](agents.md), and [Context](context.md) first.

Hooks are async callables that run at specific points in the lifecycle of turns, agents, tools, and memory. Use them for logging, metrics, auditing, or injecting fixed context into every invocation.

## Defining hooks

Decorate an async function with `@hook(type)`. The decorator registers the hook and sets its type so run-time code can select it by type.

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
| `AFTER_RUN` | After successful completion | `(turn)` |
| `ON_TIMEOUT` | Turn timed out | `(turn)` |
| `ON_ERROR` | Tool or hook raised (non-timeout) | `(turn, exception)` |
| `ON_VALUE` | Before each yielded value (streaming only) | `(turn, value)` |

**Agent** — during the agent run loop (see [Agents](agents.md#hooks)):

| Hook | When | Args |
|------|------|------|
| `BEFORE_TURN` | Before consuming next turn from queue | `(agent)` |
| `AFTER_TURN` | After turn fully processed | `(agent, turn)` |
| `ON_TURN_VALUE` | Before yielding each result | `(agent, turn, value)` |
| `ON_TURN_ERROR` | Turn raised an exception | `(agent, turn, exception)` |
| `ON_TURN_TIMEOUT` | Turn timed out | `(agent, turn)` |
| `BEFORE_PUT` | Before enqueueing a turn | `(agent, turn)` |
| `AFTER_PUT` | After enqueueing a turn | `(agent, turn)` |
| `ON_PAUSE` | When the run loop hits a paused gate | `(agent)` |
| `ON_RESUME` | After the gate is released and before the next turn | `(agent)` |

**Tool** — during tool invocation (see [Tools](tools.md#hooks)):

| Hook | When | Args |
|------|------|------|
| `BEFORE_INVOKE` | About to call the tool | `(*args, **kwargs)` |
| `ON_YIELD` | Before each yielded value (async generator tools only) | `(value)` |
| `AFTER_INVOKE` | After tool returns or finishes yielding | `(value)` — return value for coroutine tools; `list` of all yielded values for async-gen tools |

**ContextQueue** — during append (see [ContextQueue](context.md#hooks)):

| Hook | When | Args |
|------|------|------|
| `BEFORE_APPEND` | Before new items are inserted | `(items,)` — current items (read-only; does not clear or replace the window) |
| `AFTER_APPEND` | After new items have been added | `(items,)` — current items |

**ContextPool** — during pool mutation (see [Context Pool](context.md#hooks_1)):

| Hook | When | Args |
|------|------|------|
| `BEFORE_ADD` | Before item inserted (after eviction if any) | `(pool, item)` |
| `AFTER_ADD` | After item inserted | `(pool, item)` |
| `BEFORE_REMOVE` | Before item deleted | `(pool, item)` |
| `AFTER_REMOVE` | After item deleted | `(pool, item)` |
| `BEFORE_CLEAR` | Before all items cleared | `(pool)` |
| `AFTER_CLEAR` | After all items cleared | `(pool)` |

## Where hooks attach

- **Turns** — `turn.hooks.append(my_hook)`; serialized with the turn by name.
- **Agents** — `agent.hooks.append(my_hook)`; serialized with the agent by name.
- **Tools** — `@tool(hooks=[...])`; applied on every invocation of that tool.
- **ContextQueue** — `ContextQueue(limit=..., hooks=[...])` or `cq.branch(hooks=[...])`; serialized by name.
- **ContextPool** — `Agent(..., context_pool=ContextPool(hooks=[...]))` or `ContextPool(hooks=[...])`; serialized by name.

The framework selects which hooks to run via `HookRegistry.get_by_type(type, list_of_hooks)`. All hooks whose type matches the event are invoked sequentially, in the order they appear in the list. If a hook raises, execution stops and the exception propagates; later hooks in the same event are not called.

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
async def report(turn, env):
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
async def write_log(turn):
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
