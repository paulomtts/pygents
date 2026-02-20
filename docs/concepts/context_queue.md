# Context Queue

`ContextQueue` is a bounded, branchable window of context items. Use it as a building block for agent memory — e.g. working, semantic, episodic, or procedural — by passing one or more `ContextQueue` instances (or subclasses) to your agent. It holds items in a fixed-size window and automatically evicts the oldest when full.

## Creating a context queue

```python
from pygents import ContextQueue

cq = ContextQueue(limit=10)
await cq.append("user: hello")
await cq.append("assistant: hi there")
```

`limit` is the maximum number of items (must be >= 1). When a new item is appended and the window is full, the oldest item is silently dropped. `append` is async.

!!! warning "ValueError"
    `ContextQueue(limit=0)` or any `limit < 1` raises `ValueError`.

## Appending

`append` accepts variadic positional arguments:

```python
await cq.append("msg1", "msg2", "msg3")
```

## Clearing

```python
cq.clear()  # remove all items
```

## Reading items

```python
cq.items   # list copy of current items
len(cq)    # number of items
list(cq)   # iterable
bool(cq)   # False when empty
```

`items` returns a copy — mutating it does not affect the context queue.

## Branching

Agents, turns, and tools can each maintain their own context queue. A child scope inherits the parent's state via `branch()` and then diverges independently:

```python
agent_cq = ContextQueue(limit=20)
await agent_cq.append("system context", "user message")

# Turn branches from agent — gets a snapshot, then diverges
turn_cq = agent_cq.branch()
await turn_cq.append("tool call result")

# Tool branches from turn — same pattern
tool_cq = turn_cq.branch(limit=5)  # optionally smaller window
await tool_cq.append("sub-step output")

# Parent is unaffected
assert "tool call result" not in agent_cq
assert "sub-step output" not in turn_cq
```

When a child branches with a smaller `limit`, only the most recent items that fit are kept.

By default, the child inherits the parent's hooks. Pass `hooks=[]` to give the child no hooks, or `hooks=[...]` to override:

```python
cq = ContextQueue(limit=10, hooks=[my_before_append_hook])
child = cq.branch(hooks=[])           # no hooks
other = cq.branch(hooks=[other_hook]) # different hooks
```

## Hooks

`ContextQueue` supports two hook types. Pass `hooks` as a list; each hook must have `type` (e.g. from `@hook(ContextQueueHook.BEFORE_APPEND)`).

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
await cq.append("a", "b", "c")
```

If no hooks are provided, items are appended directly.

## Serialization

```python
data = cq.to_dict()   # {"limit": 10, "items": [...], "hooks": {"before_append": ["keep_recent"], ...}}
restored = ContextQueue.from_dict(data)
```

Hooks are stored by type and name (same shape as Agent/Turn). `from_dict()` resolves hook names from `HookRegistry`. Use named functions for stable cross-session serialization. If a name is not found on load, `from_dict()` raises `UnregisteredHookError`.

## Agent integration

Every agent owns a `context_queue` attribute. If you do not pass one at construction, a default `ContextQueue(limit=10)` is created automatically.

When a tool returns a `ContextItem` without an `id` (i.e. `id=None`), the agent automatically appends it to `agent.context_queue` after the turn completes. Items with an `id` set are routed to `agent.context_pool` instead.

```python
from pygents import Agent, Turn, tool
from pygents.context_pool import ContextItem

@tool()
async def summarize(text: str) -> ContextItem:
    result = ...  # call an LLM, compute a summary, etc.
    return ContextItem(content=result)  # no id → goes to context_queue

agent = Agent("summarizer", "Summarizes text", [summarize])
await agent.put(Turn("summarize", kwargs={"text": "..."}))

async for _ in agent.run():
    pass

# The summary ContextItem is now in agent.context_queue
print(agent.context_queue.items[0].content)
```

You can also pass a pre-configured queue:

```python
from pygents.context_queue import ContextQueue

agent = Agent("summarizer", "Summarizes text", [summarize], context_queue=ContextQueue(limit=20))
```

The `context_queue` is branched alongside `context_pool` when calling `agent.branch()`. It is also included in `agent.to_dict()` and restored by `Agent.from_dict()`.

## Errors

| Exception | When |
|-----------|------|
| `ValueError` | `limit < 1` at construction |
| `UnregisteredHookError` | Hook name not found in `HookRegistry` during `from_dict()` |
