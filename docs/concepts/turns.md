# Turns

A turn is a unit of work: which tool to run with what arguments. Turns describe **what** should happen; tools define how.

## Creating and running

You can pass the tool by name (string) or by reference (the callable). The turn resolves the tool from `ToolRegistry` at construction time.

```python
from pygents import Turn

# single value (tool by name)
turn = Turn("fetch", kwargs={"url": "https://example.com"}, timeout=30)
result = await turn.returning()

# by callable
turn = Turn(fetch, kwargs={"url": "https://example.com"}, timeout=30)

# with positional args
turn = Turn("fetch", args=["https://example.com"], timeout=30)
result = await turn.returning()

# streaming
turn = Turn("stream_lines", kwargs={"path": "/tmp/data.txt"})
async for line in turn.yielding():
    print(line)
```

- `returning()` — for coroutine tools, returns the tool's result
- `yielding()` — for async generator tools, yields each value

The agent's `run()` picks the right method automatically.

!!! warning "WrongRunMethodError"
    Using `returning()` on an async generator tool or `yielding()` on a coroutine tool raises `WrongRunMethodError`.

## Attributes

| Attribute | Set | Mutable while running? |
|-----------|-----|------------------------|
| `tool`, `args`, `kwargs`, `timeout` | init | No |
| `metadata` | init (optional dict) | Yes |
| `output`, `start_time`, `end_time`, `stop_reason` | by framework during/after run | Yes (by framework). `output` is the return value for coroutine tools, or a **list** of all yielded values for async generator tools. |

`start_time`, `end_time`, and `stop_reason` are **not** constructor parameters — they are managed by the framework and set during execution. On a fresh turn they default to `None`. When deserializing with `from_dict()`, the class method restores them directly on the instance.

!!! warning "SafeExecutionError"
    Changing immutable attributes while running raises `SafeExecutionError`. Calling `returning()` or `yielding()` on an already-running turn also raises `SafeExecutionError`.

!!! info "Why block reentrancy?"
    A turn cannot run twice simultaneously. Most attributes are immutable while running. This prevents race conditions from accidental reuse — if you need to run the same work again, create a new turn.

## Timeouts

Every turn has a timeout (default: 60 seconds). This prevents unbounded execution — a stuck network call or infinite loop won't block the agent forever.

For `returning()`, the timeout applies to the single await. For `yielding()`, it applies to the entire run including all yielded values.

The `stop_reason` attribute is a `StopReason` enum (importable from `pygents`):

| Outcome | `stop_reason` |
|---------|---------------|
| Success | `StopReason.COMPLETED` |
| Timeout | `StopReason.TIMEOUT` |
| Error | `StopReason.ERROR` |
| Cancelled | `StopReason.CANCELLED` |

!!! warning "TurnTimeoutError"
    When a turn exceeds its timeout, `TurnTimeoutError` is raised and `stop_reason` is set to `TIMEOUT`.

## Dynamic args and kwargs

Callable positional args and kwargs are late-evaluated: any no-arg callable passed as an arg or kwarg is called at tool invocation time, not at turn creation. This supports dynamic config, rotating tokens, memory reads, or any value that should be fresh when the tool actually runs.

```python
turn = Turn(
    "fetch",
    args=[lambda: get_current_url()],  # called when tool runs, not now
    kwargs={
        "token": lambda: get_current_token(),  # called when tool runs, not now
    },
)
```

This is especially useful when turns are queued — the lambda captures the latest state when the turn executes, not when it was created.

## Hooks

Turn hooks fire at specific points during execution. Hooks are stored as a list and selected by type at run time. Exceptions in hooks propagate.

| Hook | When | Args |
|------|------|------|
| `BEFORE_RUN` | Before tool runs (after lock acquired) | `(turn)` |
| `AFTER_RUN` | After successful completion | `(turn)` |
| `ON_TIMEOUT` | Turn timed out | `(turn)` |
| `ON_ERROR` | Tool or hook raised (non-timeout) | `(turn, exception)` |
| `ON_VALUE` | Before each yielded value (streaming only) | `(turn, value)` |

Use the `@hook(type)` decorator so the hook is registered and carries its type. Pass hooks in the constructor or append to `turn.hooks`:

```python
from pygents import Turn, hook, TurnHook

@hook(TurnHook.BEFORE_RUN)
async def log_start(turn):
    print(f"Starting {turn.tool.metadata.name}")

turn = Turn("my_tool", kwargs={}, hooks=[log_start])
```

Hooks are registered in `HookRegistry` at decoration time. Use named functions so they serialize by name.

!!! warning "ValueError"
    Registering a *different* hook with a name already in use in `HookRegistry` raises `ValueError`. Re-registering the same hook under the same name is allowed.

## Serialization

```python
data = turn.to_dict()    # dict with tool_name, args, kwargs, metadata, hooks, ...
turn = Turn.from_dict(data)  # restores from dict, resolves tool and hooks from registries
```

Datetimes are ISO strings. Hooks are serialized by name and resolved from `HookRegistry` on deserialization.

!!! warning "UnregisteredHookError"
    `Turn.from_dict()` raises `UnregisteredHookError` if a hook name is not found in `HookRegistry`.

## Errors

| Exception | When |
|-----------|------|
| `SafeExecutionError` | Changing immutable attributes or calling `returning()`/`yielding()` while running |
| `WrongRunMethodError` | `returning()` on async generator or `yielding()` on coroutine |
| `TurnTimeoutError` | Turn exceeds its timeout |
| `UnregisteredToolError` | Tool name not found in `ToolRegistry` at construction |
| `UnregisteredHookError` | Hook name not found in `HookRegistry` during `from_dict()` |
| `ValueError` | Duplicate hook name in `HookRegistry` |
