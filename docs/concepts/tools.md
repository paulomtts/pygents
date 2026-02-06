# Tools

Tools define **how** something is done. Each tool is an async function decorated with `@tool`.

## Defining tools

```python
from pygents import tool

@tool()
async def fetch(url: str) -> str:
    """Fetch a URL."""
    ...

@tool(lock=True)
async def write_file(path: str, content: str) -> None:
    """Write to a file (serialized)."""
    ...
```

!!! warning "TypeError"
    Only async functions are accepted — sync `def` raises `TypeError` at decoration time.


**Decorator parameters:**

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `lock` | `False` | If `True`, concurrent runs of this tool are serialized via `asyncio.Lock` |

Locking is opt-in because most tools are stateless and can run in parallel without contention. Use `lock=True` for tools that write to shared state (files, databases, external APIs with rate limits).

## Single-value vs streaming

**Coroutine** — returns one result, run with `returning()`:

```python
@tool()
async def summarize(text: str) -> str:
    return text[:100] + "..."
```

**Async generator** — yields a sequence, run with `yielding()`:

```python
@tool()
async def stream_lines(path: str):
    async with aiofiles.open(path) as f:
        async for line in f:
            yield line.strip()
```

The agent detects which type and calls the right method automatically.

!!! warning "WrongRunMethodError"
    Using `returning()` on an async generator or `yielding()` on a coroutine raises `WrongRunMethodError`.

## Registry

Tools register globally when decorated. The function name becomes the tool's identifier. Turns resolve tools from the registry at construction time, so a tool must be decorated before any turn references it.

```python
from pygents import ToolRegistry

my_tool = ToolRegistry.get("fetch")  # lookup by name
```

!!! warning "ValueError"
    Decorating a tool with a name that already exists in the registry raises `ValueError`. Each tool name must be unique.

!!! warning "UnregisteredToolError"
    `ToolRegistry.get(name)` raises `UnregisteredToolError` if no tool is registered with that name.

## Hooks

Tool hooks fire during invocation. Pass them when decorating:

| Hook | When | Args |
|------|------|------|
| `BEFORE_INVOKE` | About to call the tool | `(*args, **kwargs)` |
| `AFTER_INVOKE` | After tool returns/yields a value | `(value)` |

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

Tool hooks are registered in `HookRegistry` automatically and apply to **all** invocations of that tool. Exceptions in hooks propagate.

## Metadata

Access via `tool_instance.metadata`:

```python
fetch.metadata.name         # "fetch"
fetch.metadata.description  # docstring
```
