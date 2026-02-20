# Context

pygents provides two complementary primitives for agent context: `ContextQueue` and `ContextPool`. Both live in `pygents.context` alongside `ContextItem`, which is the typed wrapper used by `ContextPool` and optionally by `ContextQueue`.

| | `ContextQueue` | `ContextPool` |
|-|----------------|---------------|
| Items | Raw values (strings, dicts, `ContextItem`s, etc.) | `ContextItem` objects with `id`, `description`, `content` |
| Access | Sequential window | Keyed lookup by `id` |
| Selection | Always included (bounded window) | Application-defined: query descriptions, fetch relevant content |
| Use case | Conversation history, recent events | Documents, records, tool outputs accumulated over time |

When a tool returns a `ContextItem`, the agent routes it automatically after the turn completes: items with `id=None` go to `context_queue`; items with an `id` set go to `context_pool`.

---

## ContextItem

`ContextItem` is a frozen dataclass — immutable after creation. Only `content` is required.

```python
from pygents.context import ContextItem

item = ContextItem(
    content={"text": "..."},
    description="Q3 earnings report — revenue, margins, guidance",
    id="doc-1",
)
```

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `content` | `T` | required | The full payload |
| `description` | `str \| None` | `None` | Compact summary used by selection logic |
| `id` | `str \| None` | `None` | Unique key when stored in a pool |

!!! warning "ValueError"
    `ContextPool.add()` raises `ValueError` if the item has `id=None` or `description=None`.

---

## ContextQueue

A bounded, branchable window of items. Oldest items are evicted automatically when the window is full.

### Creating a context queue

```python
from pygents import ContextQueue
from pygents.context import ContextItem

cq = ContextQueue(limit=10)
await cq.append(ContextItem(content="user: hello"))
await cq.append(ContextItem(content="assistant: hi there"))
```

`limit` is the maximum number of items (must be >= 1). `ContextQueue` only accepts `ContextItem` instances.

!!! warning "ValueError"
    `ContextQueue(limit=0)` or any `limit < 1` raises `ValueError`.

!!! warning "TypeError"
    `append` raises `TypeError` if any argument is not a `ContextItem` instance.

### Appending

`append` accepts variadic `ContextItem` positional arguments:

```python
await cq.append(ContextItem(content="msg1"), ContextItem(content="msg2"))
```

### Clearing

```python
cq.clear()  # remove all items
```

### Reading items

```python
cq.items   # list copy of current items
len(cq)    # number of items
list(cq)   # iterable
bool(cq)   # False when empty
```

`items` returns a copy — mutating it does not affect the context queue.

### Branching

A child scope inherits the parent's state via `branch()` and then diverges independently:

```python
from pygents.context import ContextItem

agent_cq = ContextQueue(limit=20)
await agent_cq.append(ContextItem(content="system context"), ContextItem(content="user message"))

turn_cq = agent_cq.branch()
await turn_cq.append(ContextItem(content="tool call result"))

tool_cq = turn_cq.branch(limit=5)
await tool_cq.append(ContextItem(content="sub-step output"))

# Parent is unaffected
assert ContextItem(content="tool call result") not in agent_cq.items
assert ContextItem(content="sub-step output") not in turn_cq.items
```

When a child branches with a smaller `limit`, only the most recent items that fit are kept. By default, the child inherits the parent's hooks:

```python
cq = ContextQueue(limit=10, hooks=[my_before_append_hook])
child = cq.branch(hooks=[])           # no hooks
other = cq.branch(hooks=[other_hook]) # different hooks
```

### Hooks

`ContextQueue` supports two hook types. Pass `hooks` as a list; each hook must carry its type (e.g. from `@hook(ContextQueueHook.BEFORE_APPEND)`).

| Hook | When | Args |
|------|------|------|
| `BEFORE_APPEND` | Before new items are inserted | `(items,)` — current items |
| `AFTER_APPEND` | After new items have been added | `(items,)` — current items |

```python
from pygents import ContextQueue, hook, ContextQueueHook

@hook(ContextQueueHook.BEFORE_APPEND)
async def log_before(items):
    print(f"Current count: {len(items)}")

@hook(ContextQueueHook.AFTER_APPEND)
async def log_after(items):
    print(f"New count: {len(items)}")

cq = ContextQueue(limit=20, hooks=[log_before, log_after])
await cq.append(ContextItem(content="a"), ContextItem(content="b"), ContextItem(content="c"))
```

If no hooks are provided, items are appended directly.

### Serialization

```python
data = cq.to_dict()   # {"limit": 10, "items": [...], "hooks": {...}}
restored = ContextQueue.from_dict(data)
```

Hooks are stored by type and name. `from_dict()` resolves hook names from `HookRegistry`. Use named functions for stable cross-session serialization.

!!! warning "UnregisteredHookError"
    `ContextQueue.from_dict()` raises `UnregisteredHookError` if a hook name is not found in `HookRegistry`.

### Agent integration

Every agent owns a `context_queue` attribute. If you do not pass one at construction, a default `ContextQueue(limit=10)` is created automatically.

When a tool returns a `ContextItem` with `id=None`, the agent automatically appends it to `agent.context_queue` after the turn completes. Items with an `id` set are routed to `agent.context_pool` instead.

```python
from pygents import Agent, Turn, tool
from pygents.context import ContextItem

@tool()
async def summarize(text: str) -> ContextItem:
    result = ...  # call an LLM, compute a summary, etc.
    return ContextItem(content=result)  # no id → goes to context_queue

agent = Agent("summarizer", "Summarizes text", [summarize])
await agent.put(Turn("summarize", kwargs={"text": "..."}))

async for _ in agent.run():
    pass

print(agent.context_queue.items[0].content)
```

You can also pass a pre-configured queue:

```python
agent = Agent("summarizer", "Summarizes text", [summarize], context_queue=ContextQueue(limit=20))
```

The `context_queue` is branched alongside `context_pool` when calling `agent.branch()`. It is also included in `agent.to_dict()` and restored by `Agent.from_dict()`.

### Errors

| Exception | When |
|-----------|------|
| `ValueError` | `limit < 1` at construction |
| `TypeError` | `append` receives a non-`ContextItem` argument |
| `UnregisteredHookError` | Hook name not found in `HookRegistry` during `from_dict()` |

---

## ContextPool

A keyed, bounded store for `ContextItem` objects. Each item carries an `id`, a short `description`, and an arbitrary `content` payload. The agent owns writes — tools only read.

The `description` field is designed to support selective retrieval: your code can inspect descriptions to decide which items' `content` to load, without pulling everything at once. The [LLM-Driven Context Querying](../guides/context-pool.md) guide shows one approach using an LLM.

!!! tip "Write descriptions that support selection"
    `description` is what your selection logic sees before deciding whether to fetch `content`. For LLM-driven querying that means a dense, specific summary the model can reason about. `"Q3 earnings report — revenue, margins, guidance"` works; `"A document"` does not.

### How the agent uses ContextPool

When a tool returns a `ContextItem` with an `id` set, the agent automatically stores it in its `context_pool` after the turn completes. The tool itself has no knowledge of the pool — it just returns the item.

```python
from pygents import Agent, Turn, tool
from pygents.context import ContextItem

@tool()
async def fetch_doc(doc_id: str) -> ContextItem:
    content = ...  # fetch from API, DB, etc.
    return ContextItem(
        id=doc_id,
        description=f"Report {doc_id} — quarterly financials",
        content=content,
    )

agent = Agent("reader", "Reads documents", [fetch_doc])
await agent.put(Turn("fetch_doc", kwargs={"doc_id": "report-2024"}))

async for turn, value in agent.run():
    pass  # agent stored the ContextItem automatically

item = agent.context_pool.get("report-2024")
print(item.content)
```

Tools that need to read pooled items receive `pool` as a parameter — they use `pool.catalogue()`, `pool.get(id)`, or `pool.items` to access items, and never call `pool.add()`, `pool.remove()`, or `pool.clear()`.

### Creating a pool directly

```python
from pygents.context import ContextItem, ContextPool

pool = ContextPool(limit=50)
await pool.add(ContextItem(id="a", description="First", content=1))
await pool.add(ContextItem(id="b", description="Second", content=2))

item = pool.get("a")      # lookup by id
await pool.remove("a")    # remove by id
await pool.clear()        # remove all
```

`limit` caps the pool size. When the pool is full and a new item with a different `id` is added, the oldest item (by insertion order) is evicted. `add`, `remove`, and `clear` are async (they fire hooks). `get` is sync.

!!! warning "ValueError"
    `ContextPool(limit=0)` or any `limit < 1` raises `ValueError`.

!!! warning "KeyError"
    `get()` and `remove()` raise `KeyError` if the id is not present.

### Reading items

```python
pool.items       # list copy of all ContextItems
len(pool)        # number of items
list(pool)       # iterate
bool(pool)       # False when empty
pool.limit       # the configured limit (or None)
pool.catalogue() # formatted "- [id] description" string, one line per item
```

`items` returns a copy — mutating it does not affect the pool.

### Branching

```python
parent = ContextPool(limit=20)
await parent.add(ContextItem(id="x", description="Base", content=0))

child = parent.branch()          # inherits limit, items snapshot, and hooks
child2 = parent.branch(limit=5) # override limit (oldest evicted if needed)
```

The child starts with a copy of the parent's items. Mutations to either are independent. No hooks fire during the snapshot copy.

### Passing a ContextPool to Agent

```python
from pygents import Agent, ContextPoolHook, hook
from pygents.context import ContextPool

@hook(ContextPoolHook.AFTER_ADD)
async def on_item_added(pool, item):
    print(f"Added {item.id!r}: {item.description}")

agent = Agent("reader", "Reads documents", [fetch_doc], context_pool=ContextPool(hooks=[on_item_added]))
```

You can also assign a new pool directly after construction:

```python
agent.context_pool = ContextPool(limit=100, hooks=[on_item_added])
```

### Hooks

ContextPool supports six hook events. Pass `hooks` as a list to `ContextPool(...)` or via the `context_pool` parameter on `Agent`.

| Hook | When | Args |
|------|------|------|
| `BEFORE_ADD` | Before item inserted (after eviction if any) | `(pool, item)` |
| `AFTER_ADD` | After item inserted | `(pool, item)` |
| `BEFORE_REMOVE` | Before item deleted | `(pool, item)` |
| `AFTER_REMOVE` | After item deleted | `(pool, item)` |
| `BEFORE_CLEAR` | Before all items cleared | `(pool)` |
| `AFTER_CLEAR` | After all items cleared | `(pool)` |

```python
from pygents import ContextPoolHook, hook
from pygents.context import ContextPool

@hook(ContextPoolHook.BEFORE_ADD)
async def log_before(pool, item):
    print(f"About to add {item.id!r}, pool size: {len(pool)}")

@hook(ContextPoolHook.AFTER_ADD)
async def log_after(pool, item):
    print(f"Added {item.id!r}, pool size: {len(pool)}")

pool = ContextPool(limit=10, hooks=[log_before, log_after])
```

Hooks are inherited by children from `branch()`. No hooks fire during the snapshot copy inside `branch()`.

### Serialization

```python
data = pool.to_dict()         # {"limit": ..., "items": [...], "hooks": {...}}
restored = ContextPool.from_dict(data)
```

Hooks are stored by type and name (same shape as `ContextQueue`/`Agent`/`Turn`). `from_dict()` resolves hook names from `HookRegistry`. Items are restored directly without triggering hooks or eviction.

`agent.to_dict()` includes the serialized context pool. `Agent.from_dict()` restores it. Hooks in the pool are part of that roundtrip.

!!! warning "UnregisteredHookError"
    `ContextPool.from_dict()` raises `UnregisteredHookError` if a hook name is not found in `HookRegistry`.

### Errors

| Exception | When |
|-----------|------|
| `ValueError` | `limit < 1` at construction |
| `KeyError` | `get()` or `remove()` with an id not in the pool |
| `UnregisteredHookError` | Hook name not found in `HookRegistry` during `from_dict()` |

For a complete worked example of the LLM-driven query pattern, see [LLM-Driven Context Querying](../guides/context-pool.md).
