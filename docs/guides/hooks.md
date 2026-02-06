# Hooks

Hooks are async callbacks attached to turns, agents, or tools. They fire at specific points during execution. Exceptions in hooks propagate.

Register by enum key — each key holds a list of callables, awaited in order.

## HookRegistry

For serialization support, hooks must be registered in `HookRegistry`:

```python
from pygents import HookRegistry

async def my_hook(turn):
    print(f"Running {turn.tool_name}")

HookRegistry.register(my_hook)  # uses my_hook.__name__
HookRegistry.register(my_hook, name="custom_name")  # or custom name
```

The `add_hook()` method on `Turn` and `Agent` registers hooks automatically:

```python
turn.add_hook(TurnHook.BEFORE_RUN, my_hook)  # registers and appends
agent.add_hook(AgentHook.AFTER_TURN, my_hook)
```

!!! warning "ValueError"
    Registering a hook with a name that already exists raises `ValueError`.

!!! warning "UnregisteredHookError"
    `HookRegistry.get(name)` raises `UnregisteredHookError` if no hook is registered with that name. This also occurs during deserialization if a hook name is missing.

## Turn hooks

| Hook | When | Args |
|------|------|------|
| `BEFORE_RUN` | Before tool runs (after lock acquired) | `(turn)` |
| `AFTER_RUN` | After successful completion | `(turn)` |
| `ON_TIMEOUT` | Turn timed out | `(turn)` |
| `ON_ERROR` | Tool or hook raised (non-timeout) | `(turn, exception)` |
| `ON_VALUE` | Before each yielded value (streaming only) | `(turn, value)` |

```python
from pygents import TurnHook

async def log_start(turn):
    print(f"Starting {turn.tool_name}")

turn.hooks[TurnHook.BEFORE_RUN].append(log_start)
```

## Agent hooks

| Hook | When | Args |
|------|------|------|
| `BEFORE_TURN` | Before popping next turn | `(agent)` |
| `AFTER_TURN` | After turn fully processed | `(agent, turn)` |
| `ON_TURN_VALUE` | Before yielding each result | `(agent, turn, value)` |
| `ON_TURN_ERROR` | Turn raised an exception | `(agent, turn, exception)` |
| `ON_TURN_TIMEOUT` | Turn timed out | `(agent, turn)` |
| `BEFORE_PUT` | Before enqueueing a turn | `(agent, turn)` |
| `AFTER_PUT` | After enqueueing a turn | `(agent, turn)` |

```python
from pygents import AgentHook

async def on_complete(agent, turn):
    print(f"[{agent.name}] {turn.tool_name} → {turn.stop_reason}")

agent.hooks[AgentHook.AFTER_TURN].append(on_complete)
```

## Tool hooks

| Hook | When | Args |
|------|------|------|
| `BEFORE_INVOKE` | About to call the tool | `(*args, **kwargs)` |
| `AFTER_INVOKE` | After tool returns/yields a value | `(value)` |

Pass hooks when decorating:

```python
from pygents import tool, ToolHook

async def audit(*args, **kwargs):
    print(f"Called with {kwargs}")

async def log_result(value):
    print(f"Result: {value}")

@tool(hooks={
    ToolHook.BEFORE_INVOKE: [audit],
    ToolHook.AFTER_INVOKE: [log_result]
})
async def my_tool(x: int) -> int:
    return x * 2
```

Tool hooks are registered in `HookRegistry` automatically and apply to **all** invocations of that tool.

## Serialization

Hooks are serialized by name in `to_dict()` and resolved from `HookRegistry` in `from_dict()`. This enables full state restoration across process restarts.
