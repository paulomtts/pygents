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
| `hooks` | `None` | Optional list of hooks (e.g. `@hook(ToolHook.BEFORE_INVOKE)`). Applied on every invocation. |
| `**kwargs` | — | Any other keyword arguments are merged into every invocation. Call-time kwargs override these (with a warning). |

!!! info "Opt-in Locking"
    Locking is opt-in because most tools are stateless and can run in parallel without contention. Use `lock=True` for tools that write to shared state (files, databases, external APIs with rate limits).

If any decorator kwarg is a callable (e.g. a lambda), it is **evaluated at runtime** when the tool is invoked; the function receives the result. Use this for dynamic config, fresh tokens, or values that must be read at invocation time.

```python
@tool(api_key=lambda: get_config()["api_key"])
async def call_api(endpoint: str) -> str:
    ...
```

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

Tool hooks fire during invocation. Pass a list of hooks; each must have `hook_type` (e.g. from `@hook(ToolHook.BEFORE_INVOKE)`):

| Hook | When | Args |
|------|------|------|
| `BEFORE_INVOKE` | About to call the tool | `(*args, **kwargs)` |
| `ON_YIELD` | Before each yielded value (async generator tools only) | `(value)` |
| `AFTER_INVOKE` | After tool returns or finishes yielding | `(value)` — the return value or last yielded value |

```python
from pygents import tool, hook, ToolHook

@hook(ToolHook.BEFORE_INVOKE)
async def audit(*args, **kwargs):
    print(f"Called with {kwargs}")

@hook(ToolHook.AFTER_INVOKE)
async def log_result(value):
    print(f"Result: {value}")

@tool(hooks=[audit, log_result])
async def my_tool(x: int) -> int:
    return x * 2
```

Tool hooks are registered in `HookRegistry` and apply to **all** invocations of that tool. Exceptions in hooks propagate.

## Metadata

Access via `tool_instance.metadata`:

```python
fetch.metadata.name         # "fetch"
fetch.metadata.description  # docstring
```
