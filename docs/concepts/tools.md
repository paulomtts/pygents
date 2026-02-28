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
| `tags` | `None` | A list or frozenset of strings. Tags let global `@hook` declarations filter which objects they fire for — a global hook with `tags={"foo"}` only fires for tools (and agents, turns, context queues, and context pools) tagged `"foo"`. See [Tag filtering](#tag-filtering) and [Hooks — Tag filtering](hooks.md#tag-filtering). |
| `**kwargs` | — | Any other keyword arguments are merged into every invocation. Call-time kwargs override these (with a warning). |

!!! info "Opt-in Locking"
    Locking is opt-in because most tools are stateless and can run in parallel without contention. Use `lock=True` for tools that write to shared state (files, databases, external APIs with rate limits).

!!! info "Extra arguments are ignored"
    Invoking a tool with extra positional or keyword arguments does not raise. Only the parameters accepted by the tool function are forwarded. Missing required parameters still raise `TypeError` when the tool runs. This allows callers (e.g. agents or external systems) to pass a superset of arguments without errors.

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

!!! info "Tags work on all object types"
    The same `tags=` mechanism is available on `Agent`, `Turn`, `ContextQueue`, and `ContextPool` constructors, not just `@tool`. A single hook with `tags={"io"}` can filter across tools, agents, turns, and context objects at once. See [Hooks — Tag filtering](hooks.md#tag-filtering) for the full picture.

## Subtools and doc_tree

You can attach **subtools** to a tool via the method decorator `@my_tool.subtool()`. Subtools are full tools: they are registered in `ToolRegistry` under a **scoped** key so names do not collide across parents. The registry key is `"parent_name.child_name"` (and for nested subtools, `"parent.child.grandchild"`). That way `manage_users.create_user` and `manage_posts.create_user` can coexist. Use this scoped name when creating turns or looking up in the registry. `doc_tree()` and `metadata.name` keep the short name (e.g. `"create_user"`) for display.

`subtool()` accepts the same options as the top-level `@tool` decorator: `lock`, `tags`, and `**fixed_kwargs`.

```python
from pygents import tool

@tool()
async def manage_users() -> None:
    """Top-level user management tool."""
    ...

@manage_users.subtool()
async def create_user(username: str) -> None:
    """Create a new user."""
    ...

@manage_users.subtool(lock=True)
async def delete_user(username: str) -> None:
    """Delete an existing user."""
    ...

# Invoke by scoped name:
Turn("manage_users.create_user", kwargs={"username": "alice"})
ToolRegistry.get("manage_users.delete_user")
```

Use **`doc_tree()`** on any tool to get a recursive structure of name, description, and subtools (suitable for docs or LLM tool lists). It does not include runtime timing; use `metadata.dict()` for that.

```python
manage_users.doc_tree()
# {"name": "manage_users", "description": "Top-level user management tool.", "subtools": [
#   {"name": "create_user", "description": "Create a new user.", "subtools": []},
#   {"name": "delete_user", "description": "Delete an existing user.", "subtools": []},
# ]}
```

## Registry

Tools register globally when decorated. The registry key is the tool's `__name__`: for top-level tools that is the function name; for subtools it is the scoped key (e.g. `parent.child`). Turns resolve tools from the registry at construction time, so a tool must be decorated before any turn references it.

```python
from pygents import ToolRegistry

my_tool = ToolRegistry.get("fetch")  # lookup by name
all_tools = ToolRegistry.all()       # list of all registered tools
definitions = ToolRegistry.definitions()  # doc_tree() for each root-level tool (no subtools at top level)
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

Timing fields are set each time the tool runs (on the same metadata instance). `dict()` serializes datetimes to ISO strings. For a stable tree of name and description (including subtools), use `tool.doc_tree()` — see [Subtools and doc_tree](#subtools-and-doc_tree).

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
