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
    Only async functions are accepted — sync `def` raises `TypeError` at decoration time. Fixed kwargs that don't match the function's signature (and the function has no `**kwargs`) also raise `TypeError`.

**Decorator parameters:**

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `lock` | `False` | If `True`, concurrent runs of this tool are serialized via `asyncio.Lock` |
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

## Return values

When a tool runs inside an agent, the return value is routed before the next turn starts
(see [Turns](turns.md) and [Context](context.md) for the referenced types):

| Return type | Agent behavior |
|-------------|----------------|
| `Turn` | Enqueued via `put()` and executed in the same `run()` call; **not yielded to the caller** |
| `ContextItem` with `id=None` | Appended to `agent.context_queue`; **not yielded to the caller** |
| `ContextItem` with `id` set | Stored in `agent.context_pool`; **not yielded to the caller** |
| Anything else | Yielded to the caller as `(turn, value)` |

Tools intended only for internal use return `Turn` or `ContextItem`. Tools that surface results to the caller return a plain value.

```python
@tool()
async def decide(context: str) -> Turn:
    if needs_more_info(context):
        return Turn(gather, kwargs={"context": context})
    return Turn(respond, kwargs={"context": context})

@tool()
async def record(text: str) -> ContextItem:
    return ContextItem(content=text)  # id=None → appended to context_queue

@tool()
async def summarize(text: str) -> str:
    return text[:200]  # plain value → yielded to caller
```

For async generator tools, each yielded value is routed individually as it is produced — so a single generator turn can yield a mix of `ContextItem`, `Turn`, and plain values.


!!! info
    Tools can also *receive* context from the agent by declaring `ContextQueue` or `ContextPool` parameters — see [Reading context from tools](context.md#reading-context-from-tools).

---

The sections below cover advanced configuration. If you're getting started, continue to [Turns](turns.md).

## Hooks

Tool hooks fire during invocation. Attach them via method decorators after the tool is defined:

| Hook | When | Args |
|------|------|------|
| `BEFORE_INVOKE` | About to call the tool | `(**kwargs)` — same kwargs as the tool's own signature |
| `ON_YIELD` | Each yielded value (async generator tools only) | `(value)` |
| `AFTER_INVOKE` | After tool returns or finishes yielding | `(result)` — the return value (coroutine) or list of all yielded values (async gen); not dispatched if the tool raises |

```python
from pygents import tool

@tool()
async def my_tool(x: int) -> int:
    return x * 2

@my_tool.before_invoke
async def audit(x: int) -> None:
    print(f"Called with x={x}")

@my_tool.after_invoke
async def log_result(result: int) -> None:
    print(f"Result: {result}")
```

Use `@my_tool.on_yield` for async generator tools. Hooks attached this way fire only for that specific tool instance. For process-wide hooks that fire for every tool, use `@hook(ToolHook.*)` (see [Hooks](hooks.md)). Exceptions in hooks propagate.

## Registry

Tools register globally when decorated. The function name becomes the tool's identifier. Turns resolve tools from the registry at construction time, so a tool must be decorated before any turn references it.

```python
from pygents import ToolRegistry

my_tool = ToolRegistry.get("fetch")  # lookup by name
all_tools = ToolRegistry.all()       # list of all registered tools
```

!!! warning "ValueError"
    Decorating a tool with a name that already exists in the registry raises `ValueError`. Each tool name must be unique.

!!! warning "UnregisteredToolError"
    `ToolRegistry.get(name)` raises `UnregisteredToolError` if no tool is registered with that name.

## Metadata and timing

Each tool has a `metadata` attribute (`ToolMetadata`) with name, description, and execution timing:

```python
fetch.metadata.name         # "fetch"
fetch.metadata.description  # docstring
fetch.metadata.start_time   # datetime — set on entry, None before first run
fetch.metadata.end_time     # datetime — set in finally, None before first run
fetch.metadata.dict()       # {"name": ..., "description": ..., "start_time": ..., "end_time": ...}
```

Timing fields are set each time the tool runs (on the same metadata instance). `dict()` serializes datetimes to ISO strings.

## Protocol

The `Tool` protocol defines the shape every decorated tool conforms to:

```python
class Tool(Protocol):
    metadata: ToolMetadata
    fn: Callable[..., Coroutine | AsyncIterator]
    hooks: list[Hook]
    lock: asyncio.Lock | None
    def __call__(self, *args, **kwargs) -> Any: ...
```

## Errors

| Exception | When |
|-----------|------|
| `TypeError` | Decorating a sync function, or fixed kwargs not in signature |
| `ValueError` | Duplicate tool name in `ToolRegistry` |
| `UnregisteredToolError` | `ToolRegistry.get()` with unknown name |
| `WrongRunMethodError` | `returning()` on async generator or `yielding()` on coroutine |
