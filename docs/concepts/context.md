# Context Pool

## Design intent

A long-running agent accumulates a lot of context — fetched documents, database records, code snippets, prior tool outputs. The naive approach is to serialize all of it into every prompt, but this quickly hits token limits and increases cost even when most of the context is irrelevant to the current step.

`ContextPool` is a specialized container that solves this problem. It stores items out-of-prompt and allows the agent to **query the pool with the LLM** — presenting only the lightweight `description` of each item, asking the model which ones are relevant, and fetching only those items' `content` to include in the actual prompt. The full payloads never leave the pool unless the LLM says they're needed.

This keeps every prompt tight: the LLM sees descriptions (a sentence or two each) rather than full content until it explicitly selects items. The pattern scales gracefully — a pool of 200 items adds only a handful of description lines to a selection prompt, not 200 pages of content.

`ContextPool` is distinct from `Memory`:

| | `Memory` | `ContextPool` |
|-|----------|---------------|
| Items | Raw values (strings, dicts, etc.) | `ContextItem` objects with `id`, `description`, `content` |
| Access | Sequential window | Keyed lookup by `id` |
| Selection | Always included (bounded window) | LLM-driven: query descriptions, fetch relevant content |
| Use case | Conversation history, recent events | Documents, records, tool outputs accumulated over time |

---

## ContextItem

```python
from pygents.context import ContextItem

item = ContextItem(id="doc-1", description="Q3 earnings report — revenue, margins, guidance", content={"text": "..."})
```

| Field | Type | Meaning |
|-------|------|---------|
| `id` | `str` | Unique key in the pool |
| `description` | `str` | Compact summary the LLM reads during selection — keep it one or two sentences |
| `content` | `T` | The full payload, injected into prompts only when selected |

`ContextItem` is a frozen dataclass — it is immutable after creation.

!!! tip "Write descriptions for the LLM, not for humans"
    The `description` is the only part of an item the LLM sees during context selection. Make it dense and specific enough that the model can decide whether the item is relevant without seeing the content. `"Q3 earnings report — revenue, margins, guidance"` works; `"A document"` does not.

## The query pattern

Tools never write to the pool — that is the agent's job (via auto-pooling from tools that return `ContextItem`). Tools that need context receive `pool` as a parameter and **read** from it.

The canonical read pattern is two steps:

1. **Selection** — build a prompt listing `id + description` for every item in the pool (descriptions only, no content), call the LLM with a response model that returns `list[str]` (selected ids).
2. **Injection** — fetch the `content` of selected items with `pool.get(id)` and include it in the actual working prompt.

```python
from pydantic import BaseModel
from pygents.context import ContextPool

class ContextSelection(BaseModel):
    """IDs of the context items relevant to the current task."""
    relevant_ids: list[str]

# In a @tool function that receives `pool` and `memory` as parameters:
catalogue = pool.catalogue()        # descriptions only — no content sent here
selection = await llm.asend(
    response_model=ContextSelection,
    template="Task: {{ task }}\n\nAvailable context:\n{{ catalogue }}\n\n"
             "Return the IDs of the items that are relevant to this task.",
    task=task,
    catalogue=catalogue,
)

# Fetch content only for selected items
pool_ids = {item.id for item in pool.items}
selected = [pool.get(id) for id in selection.relevant_ids if id in pool_ids]
```

Only the selected items' `content` is then passed to the tool that generates the final answer. See the [LLM-Driven Context Querying](../guides/context-pool.md) guide for a complete, runnable example.

---

## How the agent uses ContextPool

When a tool returns a `ContextItem`, the agent automatically stores it in its `context_pool` after the turn completes. The tool itself has no knowledge of the pool — it just returns the item.

```python
from pygents import Agent, Turn, tool
from pygents.context import ContextItem

@tool()
async def fetch_doc(doc_id: str) -> ContextItem:
    content = ...  # fetch from API, DB, etc.
    return ContextItem(
        id=doc_id,
        description=f"Report {doc_id} — quarterly financials",  # LLM reads this during selection
        content=content,                                          # LLM reads this only when selected
    )

agent = Agent("reader", "Reads documents", [fetch_doc])
await agent.put(Turn("fetch_doc", kwargs={"doc_id": "report-2024"}))

async for turn, value in agent.run():
    pass  # agent stored the ContextItem automatically

# Retrieve anytime after the turn
item = agent.context_pool.get("report-2024")
print(item.content)
```

This is the only way items enter the pool from tool code. Tools that need to use pooled items receive `pool` as a parameter and **read** from it — they never call `pool.add()`, `pool.remove()`, or `pool.clear()`.

## Creating a pool directly

You can also construct and use `ContextPool` standalone:

```python
from pygents.context import ContextItem, ContextPool

pool = ContextPool(limit=50)
await pool.add(ContextItem(id="a", description="First", content=1))
await pool.add(ContextItem(id="b", description="Second", content=2))

item = pool.get("a")      # lookup by id
await pool.remove("a")    # remove by id
await pool.clear()        # remove all
```

`limit` caps the pool size. When the pool is full and a new item with a different `id` is added, the oldest item (by insertion order) is evicted. `add`, `remove`, and `clear` are all async (they fire hooks). `get` is sync.

!!! warning "ValueError"
    `ContextPool(limit=0)` or any `limit < 1` raises `ValueError`.

!!! warning "KeyError"
    `get()` and `remove()` raise `KeyError` if the id is not present.

## Reading items

```python
pool.items       # list copy of all ContextItems
len(pool)        # number of items
list(pool)       # iterate
bool(pool)       # False when empty
pool.limit       # the configured limit (or None)
pool.catalogue() # formatted "- [id] description" string, one line per item
```

`items` returns a copy — mutating it does not affect the pool.

## Branching

```python
parent = ContextPool(limit=20)
await parent.add(ContextItem(id="x", description="Base", content=0))

child = parent.branch()           # inherits limit, items snapshot, and hooks
child2 = parent.branch(limit=5)  # override limit (oldest evicted if needed)
```

The child starts with a copy of the parent's items. Mutations to either are independent. No hooks fire during the snapshot copy.

## Passing a ContextPool to Agent

The simplest way to attach hooks to an agent's pool is via the constructor:

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
from pygents.context import ContextPool

agent.context_pool = ContextPool(limit=100, hooks=[on_item_added])
```

## Hooks

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

## Serialization

```python
data = pool.to_dict()         # {"limit": ..., "items": [...], "hooks": {...}}
restored = ContextPool.from_dict(data)
```

Hooks are stored by type and name (same shape as `Memory`/`Agent`/`Turn`). `from_dict()` resolves hook names from `HookRegistry`. Items are restored directly without triggering hooks or eviction.

!!! warning "UnregisteredHookError"
    `ContextPool.from_dict()` raises `UnregisteredHookError` if a hook name is not found in `HookRegistry`.

## Serialization roundtrip via Agent

`agent.to_dict()` includes the serialized context pool. `Agent.from_dict()` restores it. Hooks in the pool are part of that roundtrip.

## Errors

| Exception | When |
|-----------|------|
| `ValueError` | `limit < 1` at construction |
| `KeyError` | `get()` or `remove()` with an id not in the pool |
| `UnregisteredHookError` | Hook name not found in `HookRegistry` during `from_dict()` |

For a complete worked example of the LLM-driven query pattern, see [LLM-Driven Context Querying](../guides/context-pool.md).
