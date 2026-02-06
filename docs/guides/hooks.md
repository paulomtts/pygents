# Hooks

Hooks are async callbacks attached to turns, agents, or tools. They fire at specific points during execution. Exceptions in hooks propagate.

Register by enum key — each key holds a list of callables, awaited in order.

## Turn hooks

| Hook | When | Args |
|------|------|------|
| `BEFORE_RUN` | Before tool runs (after lock acquired) | `(turn)` |
| `AFTER_RUN` | After successful completion | `(turn)` |
| `ON_TIMEOUT` | Turn timed out | `(turn)` |
| `ON_ERROR` | Tool or hook raised (non-timeout) | `(turn, exception)` |
| `ON_VALUE` | Before each yielded value (streaming only) | `(turn, value)` |

```python
from pygents import TurnHook

async def log_start(turn):
    print(f"Starting {turn.tool_name}")

turn.hooks[TurnHook.BEFORE_RUN].append(log_start)
```

## Agent hooks

| Hook | When | Args |
|------|------|------|
| `BEFORE_TURN` | Before popping next turn | `(agent)` |
| `AFTER_TURN` | After turn fully processed | `(agent, turn)` |
| `ON_TURN_VALUE` | Before yielding each result | `(agent, turn, value)` |
| `ON_TURN_ERROR` | Turn raised an exception | `(agent, turn, exception)` |
| `ON_TURN_TIMEOUT` | Turn timed out | `(agent, turn)` |
| `BEFORE_PUT` | Before enqueueing a turn | `(agent, turn)` |
| `AFTER_PUT` | After enqueueing a turn | `(agent, turn)` |

```python
from pygents import AgentHook

async def on_complete(agent, turn):
    print(f"[{agent.name}] {turn.tool_name} → {turn.stop_reason}")

agent.hooks[AgentHook.AFTER_TURN].append(on_complete)
```

## Tool hooks

| Hook | When | Args |
|------|------|------|
| `BEFORE_INVOKE` | About to call the tool | `(turn, kwargs)` |
| `AFTER_INVOKE` | After tool returns/yields a value | `(turn, value)` |

```python
from pygents import ToolHook

async def audit(turn, kwargs):
    print(f"Calling {turn.tool_name} with {kwargs}")

my_tool.hooks[ToolHook.BEFORE_INVOKE].append(audit)
```

Tool hooks apply to **all** invocations of that tool since agents share the same tool instance.

Hooks are not included in `to_dict()` / `from_dict()` serialization.
