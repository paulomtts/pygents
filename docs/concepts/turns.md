# Turns

## Role

A **turn** is a single unit of work: which tool to run and with what arguments. It describes **what** should happen; the tool defines how. Turns are created with a tool name and kwargs; the tool is resolved from `ToolRegistry` at init. You can run a turn yourself (`returning()` / `yielding()`) or enqueue it on an agent and let the agent run it.

## Creating and running

```python
from app import Turn

turn = Turn("fetch", kwargs={"url": "https://example.com"}, timeout=30)
result = await turn.returning()   # single value
# or
async for chunk in turn.yielding():   # streaming; use only if tool is async gen
    ...
```

- **`returning()`** — Use for coroutine tools. Returns the tool’s return value. One shot.
- **`yielding()`** — Use for async-generator tools. Yields each value from the tool. Using `returning()` on an async-gen (or `yielding()` on a coroutine) raises `WrongRunMethodError`.

The agent’s `run()` chooses the method automatically based on `inspect.isasyncgenfunction(turn.tool.fn)`.

## Attributes

| Attribute | Set at init / when | Mutable while running? |
|-----------|--------------------|-------------------------|
| `uuid` | init (or generated) | No |
| `tool_name`, `tool`, `kwargs`, `timeout` | init | No |
| `metadata` | init | Yes |
| `output`, `start_time`, `end_time`, `stop_reason` | During/after run | Yes |

Changing other attributes while the turn is running raises `SafeExecutionError`. Starting `returning()` or `yielding()` again while the same turn is already running also raises `SafeExecutionError`.

## Timeouts and outcome

Each turn has a `timeout` (default 60 seconds). For `returning()` it applies to the single await; for `yielding()` it applies to the whole run. On timeout, `stop_reason` is set to `StopReason.TIMEOUT`, end time is set, and `TurnTimeoutError` is raised. On tool/hook exception, `stop_reason` is `StopReason.ERROR` and the exception is re-raised. On success, `stop_reason` is `StopReason.COMPLETED` and `output` holds the result (or the list of yielded values for `yielding()`).

## Dynamic kwargs

Any kwarg value that is a no-arg callable (e.g. `lambda: x`) is **not** evaluated at turn creation. It is evaluated when the tool is invoked inside `returning()` or `yielding()`, and the result is passed to the tool. Use this for values that must be read at run time (e.g. config, memory).

## Serialization

`turn.to_dict()` returns a dict with `uuid`, `tool_name`, `kwargs` (evaluated), `metadata`, `timeout`, `start_time`, `end_time`, `stop_reason`, `output`. Datetimes are ISO strings. `Turn.from_dict(data)` restores a turn; the tool is resolved by `tool_name`. Hooks are not stored.
