# Tools

Tools define **how** something is done. Each tool is an async function decorated with `@tool`.

## Defining tools

```python
from pygents import tool, ToolType

@tool()
async def fetch(url: str) -> str:
    """Fetch a URL."""
    ...

@tool(type=ToolType.ACTION, approval=True)
async def delete_resource(id: str) -> None:
    """Delete a resource (requires approval)."""
    ...
```

**Decorator parameters:**

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `type` | `ToolType.ACTION` | One of `REASONING`, `ACTION`, `MEMORY_READ`, `MEMORY_WRITE`, `COMPLETION_CHECK` |
| `approval` | `False` | Metadata flag (no built-in enforcement) |
| `lock` | `False` | If `True`, concurrent runs of this tool are serialized via `asyncio.Lock` |

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

The agent detects which type and calls the right method. Using the wrong one raises `WrongRunMethodError`.

## Completion checks

A tool with `type=ToolType.COMPLETION_CHECK` signals the agent to stop its run loop when it returns `True`. It must be a coroutine with `-> bool`:

```python
@tool(type=ToolType.COMPLETION_CHECK)
async def is_done() -> bool:
    return all_tasks_finished()
```

The decorator enforces both constraints at registration time. If the agent receives a non-bool output, it raises `CompletionCheckReturnError`.

## Registry

Tools register automatically when decorated. Duplicate names raise `ValueError`.

```python
from pygents import ToolRegistry

my_tool = ToolRegistry.get("fetch")  # lookup by name
```

## Metadata

Access via `tool_instance.metadata`:

```python
fetch.metadata.name         # "fetch"
fetch.metadata.description  # docstring
fetch.metadata.type         # ToolType.ACTION
fetch.metadata.approval     # False
```
