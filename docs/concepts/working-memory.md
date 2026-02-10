# Working Memory

Working memory is a bounded, branchable window of context items. It holds the current-turn state — messages, tool outputs, intermediate reasoning — and automatically evicts the oldest items when the window is full.

## Creating a memory

```python
from pygents import WorkingMemory

mem = WorkingMemory(limit=10)
mem.append("user: hello")
mem.append("assistant: hi there")
```

`limit` is the maximum number of items. When a new item is appended and the window is full, the oldest item is silently dropped.

## Appending multiple items

`append` accepts variadic positional arguments:

```python
mem.append("msg1", "msg2", "msg3")
```

## Compaction

You can pass a `compact` callback to `append`. It receives the current items and returns a (potentially shorter) list that replaces the window contents **before** the new items are inserted:

```python
mem.append(
    new_message,
    compact=lambda items: [summarize(items[:-2])] + items[-2:],
)
```

The compacted result is still subject to the window limit — if it exceeds `limit`, the oldest entries are evicted as usual. If `compact` is not provided, items are appended directly.

## Branching

Agents, turns, and tools can each maintain their own working memory. A child scope inherits the parent's state via `branch()` and then diverges independently:

```python
agent_mem = WorkingMemory(limit=20)
agent_mem.append("system context", "user message")

# Turn branches from agent — gets a snapshot, then diverges
turn_mem = agent_mem.branch()
turn_mem.append("tool call result")

# Tool branches from turn — same pattern
tool_mem = turn_mem.branch(limit=5)  # optionally smaller window
tool_mem.append("sub-step output")

# Parent is unaffected
assert "tool call result" not in agent_mem
assert "sub-step output" not in turn_mem
```

When a child branches with a smaller `limit`, only the most recent items that fit are kept.

## Reading items

```python
mem.items   # list copy of current items
len(mem)    # number of items
list(mem)   # iterable
bool(mem)   # False when empty
```

`items` returns a copy — mutating it does not affect the memory.

## Serialization

```python
data = mem.to_dict()              # {"limit": 10, "items": [...]}
restored = WorkingMemory.from_dict(data)
```
