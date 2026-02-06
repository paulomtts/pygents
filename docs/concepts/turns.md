# Turns

A turn is a unit of work: which tool to run with what arguments. Turns describe **what** should happen; tools define how.

## Creating and running

```python
from pygents import Turn

# single value
turn = Turn("fetch", kwargs={"url": "https://example.com"}, timeout=30)
result = await turn.returning()

# streaming
turn = Turn("stream_lines", kwargs={"path": "/tmp/data.txt"})
async for line in turn.yielding():
    print(line)
```

- `returning()` — for coroutine tools, returns the tool's result
- `yielding()` — for async generator tools, yields each value

The agent's `run()` picks the right method automatically.

## Attributes

| Attribute | Set | Mutable while running? |
|-----------|-----|------------------------|
| `uuid` | init (auto-generated) | No |
| `tool_name`, `tool`, `kwargs`, `timeout` | init | No |
| `metadata` | init (optional dict) | Yes |
| `output`, `start_time`, `end_time`, `stop_reason` | during/after run | Yes (by framework) |

Changing immutable attributes while running raises `SafeExecutionError`. Calling `returning()` or `yielding()` on an already-running turn also raises `SafeExecutionError`.

## Timeouts

Default: 60 seconds. For `returning()`, applies to the single await. For `yielding()`, applies to the entire run.

| Outcome | `stop_reason` | Exception |
|---------|---------------|-----------|
| Success | `COMPLETED` | — |
| Timeout | `TIMEOUT` | `TurnTimeoutError` |
| Error | `ERROR` | re-raised |

## Dynamic kwargs

Callable kwargs are evaluated at invocation time, not at turn creation:

```python
turn = Turn("fetch", kwargs={
    "url": "https://example.com",
    "token": lambda: get_current_token(),  # called when tool runs
})
```

## Serialization

```python
data = turn.to_dict()    # dict with uuid, tool_name, kwargs, metadata, ...
turn = Turn.from_dict(data)  # restores from dict, resolves tool from registry
```

Datetimes are ISO strings. Hooks are not serialized.
