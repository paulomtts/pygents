# Turns

A turn is a unit of work: which tool to run with what arguments. Turns describe **what** should happen; tools define how.

## Creating and running

You can pass the tool by name (string) or by reference (the callable). The turn resolves the tool from `ToolRegistry` at construction time.

!!! warning "UnregisteredToolError"
    `Turn(name, ...)` raises `UnregisteredToolError` if no tool with that name is found in `ToolRegistry`. Tools must be decorated before any turn references them.

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
| `tags` | init | No. A `frozenset[str]` of labels used to filter global `@hook` declarations. Empty by default. See [Hooks — Tag filtering](hooks.md#tag-filtering). |
| `output` | by framework after run | Yes. The return value for coroutine tools, or a **list** of all yielded values for async generator tools. `None` on a fresh turn. |
| `metadata` | by framework during run | Yes. A `TurnMetadata` dataclass with three fields: `start_time`, `end_time`, `stop_reason`. All default to `None` on a fresh turn. |

Access execution results via the metadata object:

```python
turn.output                    # the tool's return value (or list for generators)
turn.metadata.start_time       # datetime set when the turn started
turn.metadata.end_time         # datetime set when the turn finished
turn.metadata.stop_reason      # StopReason enum value
```

`metadata` fields are not constructor parameters — they are managed by the framework and set during execution. `from_dict()` restores them directly.

!!! warning "SafeExecutionError"
    Changing immutable attributes while running raises `SafeExecutionError`. Calling `returning()` or `yielding()` on an already-running turn also raises `SafeExecutionError`.

!!! info "Why block reentrancy?"
    A turn cannot run twice simultaneously. Most attributes are immutable while running. This prevents race conditions from accidental reuse — if you need to run the same work again, create a new turn.

## Timeouts

Every turn has a timeout (default: 60 seconds). This prevents unbounded execution — a stuck network call or infinite loop won't block the agent forever.

For `returning()`, the timeout applies to the single await. For `yielding()`, it applies to the entire run including all yielded values.

`turn.metadata.stop_reason` is a `StopReason` enum (importable from `pygents`):

| Outcome | `stop_reason` |
|---------|---------------|
| Success | `StopReason.COMPLETED` |
| Timeout | `StopReason.TIMEOUT` |
| Error | `StopReason.ERROR` |
| Cancelled | `StopReason.CANCELLED` |

!!! warning "TurnTimeoutError"
    When a turn exceeds its timeout, `TurnTimeoutError` is raised and `turn.metadata.stop_reason` is set to `TIMEOUT`.

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

---

The sections below cover advanced configuration. If you're getting started, continue to [Agents](agents.md).

## Hooks

Turn hooks fire at specific points during execution. Hooks are stored as a list and selected by type at run time. Exceptions in hooks propagate.

| Hook | When | Args |
|------|------|------|
| `BEFORE_RUN` | Before tool runs (after lock acquired) | `(turn)` |
| `AFTER_RUN` | After successful completion | `(turn, output)` |
| `ON_TIMEOUT` | Turn timed out | `(turn)` |
| `ON_ERROR` | Tool or hook raised (non-timeout) | `(turn, exception)` |
| `ON_COMPLETE` | Always fires in finally block (clean, error, or timeout) | `(turn, stop_reason)` |

Use the `@hook(type)` decorator so the hook is registered and carries its type. Pass hooks in the constructor:

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
