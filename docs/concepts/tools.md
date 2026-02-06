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

**Decorator parameters:**

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `lock` | `False` | If `True`, concurrent runs of this tool are serialized via `asyncio.Lock` |

!!! warning "TypeError"
    Only async functions are accepted — sync `def` raises `TypeError` at decoration time.

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

Tools register automatically when decorated.

```python
from pygents import ToolRegistry

my_tool = ToolRegistry.get("fetch")  # lookup by name
```

!!! warning "ValueError"
    Decorating a tool with a name that already exists in the registry raises `ValueError`.

!!! warning "UnregisteredToolError"
    `ToolRegistry.get(name)` raises `UnregisteredToolError` if no tool is registered with that name.

## Metadata

Access via `tool_instance.metadata`:

```python
fetch.metadata.name         # "fetch"
fetch.metadata.description  # docstring
```
