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
| `lock` | `False` | If `True`, concurrent runs of this tool are serialized via `asyncio.Lock`. The lock covers only the actual function call (and, for async-gen tools, the iteration loop including `ON_YIELD`). Lifecycle hooks (`BEFORE_INVOKE`, `AFTER_INVOKE`, `ON_ERROR`) run outside the lock. |
| `tags` | `None` | A list or frozenset of strings. Tags let global `@hook` declarations filter which tools they fire for — a global hook with `tags={"foo"}` only fires for tools tagged `"foo"`. See [Tag filtering](#tag-filtering). |
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
| `AFTER_INVOKE` | After tool returns or finishes yielding | `(result)` — the return value (coroutine) or list of all yielded values (async gen); fires with partial list on early break; not dispatched if the tool raises |
| `ON_ERROR` | Tool raised an exception | `(exc=exc)` — the exception; not dispatched on success; `AFTER_INVOKE` does not fire when `ON_ERROR` fires |

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

@my_tool.on_error
async def handle_error(exc: Exception) -> None:
    print(f"Tool failed: {exc}")
```

Use `@my_tool.on_yield` for async generator tools. Hooks attached this way fire only for that specific tool instance. For process-wide hooks that fire for every tool, use `@hook(ToolHook.*)` (see [Hooks](hooks.md)). Exceptions in hooks propagate.

## Tag filtering

Tags let you scope global `@hook` declarations to a subset of tools without writing conditional logic inside the hook body.

Assign tags at decoration time:

```python
@tool(tags=["storage", "io"])
async def write_db(record: dict) -> None:
    ...

@tool(tags=["io"])
async def read_file(path: str) -> str:
    ...

@tool()
async def compute(x: int) -> int:
    ...
```

Then filter global hooks with `tags=`:

```python
from pygents import hook, ToolHook

@hook(ToolHook.AFTER_INVOKE, tags={"io"})
async def log_io_ops(result) -> None:
    print(f"I/O op returned: {result}")
```

`log_io_ops` fires after `write_db` and `read_file` (both tagged `"io"`), but **not** after `compute` (no tags). The match is **OR semantics**: the hook fires if the tool has at least one tag in the hook's `tags` set.

A global hook with **no** `tags` argument fires for all tools, regardless of whether they have tags:

```python
@hook(ToolHook.AFTER_INVOKE)
async def log_all(result) -> None:
    print(f"Any tool returned: {result}")
```

Instance-scoped hooks (`@my_tool.after_invoke`) are unaffected by tag filtering — they always fire for their specific tool.

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
    tags: frozenset[str]
    def __call__(self, *args, **kwargs) -> Any: ...
```

## Errors

| Exception | When |
|-----------|------|
| `TypeError` | Decorating a sync function, or fixed kwargs not in signature |
| `ValueError` | Duplicate tool name in `ToolRegistry` |
| `UnregisteredToolError` | `ToolRegistry.get()` with unknown name |
| `WrongRunMethodError` | `returning()` on async generator or `yielding()` on coroutine |
