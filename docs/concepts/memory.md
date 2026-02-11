# Memory

Memory is a bounded, branchable window of context items. Use it as a building block for agent memory — e.g. working, semantic, episodic, or procedural — by passing one or more `Memory` instances (or subclasses) to your agent. It holds items in a fixed-size window and automatically evicts the oldest when full.

## Creating a memory

```python
from pygents import Memory

mem = Memory(limit=10)
await mem.append("user: hello")
await mem.append("assistant: hi there")
```

`limit` is the maximum number of items. When a new item is appended and the window is full, the oldest item is silently dropped. `append` is async.

## Appending multiple items

`append` accepts variadic positional arguments:

```python
await mem.append("msg1", "msg2", "msg3")
```

## Hooks (BEFORE_APPEND, AFTER_APPEND)

Memory supports two hook types. Pass `hooks` as a list; each hook must have `hook_type` (e.g. from `@hook(MemoryHook.BEFORE_APPEND)`).

| Hook | When | Args |
|------|------|------|
| `BEFORE_APPEND` | Before new items are inserted | `(items,)` — current items |
| `AFTER_APPEND` | After new items have been added | `(items,)` — current items |

```python
from pygents import Memory, hook, MemoryHook

@hook(MemoryHook.BEFORE_APPEND)
async def log_before(items):
    print(f"Current count: {len(items)}")

@hook(MemoryHook.AFTER_APPEND)
async def log_after(items):
    print(f"New count: {len(items)}")

mem = Memory(limit=20, hooks=[log_before, log_after])
await mem.append("a", "b", "c")
```

If no hooks are provided, items are appended directly.

## Branching

Agents, turns, and tools can each maintain their own memory. A child scope inherits the parent's state via `branch()` and then diverges independently:

```python
agent_mem = Memory(limit=20)
await agent_mem.append("system context", "user message")

# Turn branches from agent — gets a snapshot, then diverges
turn_mem = agent_mem.branch()
await turn_mem.append("tool call result")

# Tool branches from turn — same pattern
tool_mem = turn_mem.branch(limit=5)  # optionally smaller window
await tool_mem.append("sub-step output")

# Parent is unaffected
assert "tool call result" not in agent_mem
assert "sub-step output" not in turn_mem
```

When a child branches with a smaller `limit`, only the most recent items that fit are kept.

By default, the child inherits the parent's hooks. Pass `hooks=[]` to give the child no hooks, or `hooks=[...]` to override:

```python
mem = Memory(limit=10, hooks=[my_before_append_hook])
child = mem.branch(hooks=[])           # no hooks
other = mem.branch(hooks=[other_hook]) # different hooks
```

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
data = mem.to_dict()   # {"limit": 10, "items": [...], "hooks": {"before_append": ["keep_recent"], ...}}
restored = Memory.from_dict(data)
```

Hooks are stored by type and name (same shape as Agent/Turn). `from_dict()` resolves hook names from `HookRegistry`. Use named functions for stable cross-session serialization. If a name is not found on load, `from_dict()` raises `UnregisteredHookError`.
