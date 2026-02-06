# Tools

## Role

Tools describe **how** something is done. Each tool is an async function (coroutine) or async generator, registered by name. Turns reference tools by name and supply kwargs; the registry resolves the tool at turn creation.

## Defining tools

Use the `@tool` decorator. The function must be async (coroutine or async generator). Docstring becomes the tool description.

```python
from app import tool, ToolType

@tool(type=ToolType.ACTION)
async def fetch(url: str) -> str:
    """Fetch a URL."""
    ...

@tool(type=ToolType.ACTION, approval=True)
async def delete_resource(id: str) -> None:
    """Delete a resource (requires approval)."""
    ...
```

**Parameters:**

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `type` | `ToolType.ACTION` | One of `REASONING`, `ACTION`, `MEMORY_READ`, `MEMORY_WRITE`, `COMPLETION_CHECK`. |
| `approval` | `False` | Metadata only; no built-in enforcement. |
| `lock` | `False` | If `True`, concurrent runs of this tool are serialized with an asyncio lock. |

## Single-value vs streaming

- **Coroutine:** Returns one result. The turn is run with `returning()` (or the agent does this for you).
- **Async generator:** Yields a sequence of values. The turn must be run with `yielding()`; the agent does this when it detects an async-gen tool.

Using the wrong run method (e.g. `returning()` on an async-gen tool) raises `WrongRunMethodError`.

## Completion-check tools

A tool with `type=ToolType.COMPLETION_CHECK` must be a **coroutine** (not an async generator) with return annotation `-> bool`. The decorator enforces this at registration. The agent uses it to decide when to stop the run loop: when such a turn’s output is `True`, the loop exits. If the output is not a bool, the agent raises `CompletionCheckReturnError`.

## Registry

Tools register automatically when decorated. `ToolRegistry.get(name)` returns the tool; duplicate names raise `ValueError`. Agents receive a list of tool instances; each must be the same object as `ToolRegistry.get(t.metadata.name)` or the agent constructor raises `ValueError`.

## Protocol and metadata

The public protocol is `Tool`: `metadata` (`ToolMetadata`: name, description, type, approval), `fn`, and `lock`. Access metadata via `tool_inst.metadata`.
