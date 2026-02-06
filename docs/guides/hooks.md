# Hooks

Hooks are async callbacks you attach to a turn, agent, or tool. They are invoked at specific points; exceptions propagate. Register by enum key: `turn.hooks[TurnHook.BEFORE_RUN] = [my_async_fn]`, and similarly for `agent.hooks` and `tool.hooks`. Each key holds a **list** of callables; all are awaited in order.

## Turn hooks

| Hook | When | Arguments |
|------|------|-----------|
| `TurnHook.BEFORE_RUN` | Before the tool runs (after lock acquired) | `(turn)` |
| `TurnHook.AFTER_RUN` | After run completes successfully | `(turn)` |
| `TurnHook.ON_TIMEOUT` | When the turn times out | `(turn)` |
| `TurnHook.ON_ERROR` | When the tool or a hook raises (non-timeout) | `(turn, exception)` |
| `TurnHook.ON_VALUE` | For yielding runs only; before each value is yielded | `(turn, value)` |

Attach on the turn instance: `turn.hooks[TurnHook.BEFORE_RUN].append(callback)`.

## Agent hooks

| Hook | When | Arguments |
|------|------|-----------|
| `AgentHook.BEFORE_TURN` | Before popping the next turn | `(agent)` |
| `AgentHook.AFTER_TURN` | After a turn is fully processed | `(agent, turn)` |
| `AgentHook.ON_TURN_VALUE` | For each `(turn, value)` before it is yielded from `run()` | `(agent, turn, value)` |
| `AgentHook.ON_TURN_ERROR` | When a turn raises | `(agent, turn, exception)` |
| `AgentHook.ON_TURN_TIMEOUT` | When a turn times out | `(agent, turn)` |
| `AgentHook.BEFORE_PUT` | Before putting a turn on the queue | `(agent, turn)` |
| `AgentHook.AFTER_PUT` | After putting a turn on the queue | `(agent, turn)` |

Attach on the agent: `agent.hooks[AgentHook.AFTER_TURN].append(callback)`.

## Tool hooks

| Hook | When | Arguments |
|------|------|-----------|
| `ToolHook.BEFORE_INVOKE` | When a turn is about to call the tool | `(turn, kwargs)` |
| `ToolHook.AFTER_INVOKE` | After the tool returns (once per single-value; per value for yielding) | `(turn, value)` |

Attach on the tool: `my_tool.hooks[ToolHook.BEFORE_INVOKE].append(callback)`. The same tool instance is used by every turn that runs it, so tool hooks apply to all invocations of that tool.

## Example

```python
async def on_after_turn(agent, turn):
    print(f"Turn {turn.uuid} finished: {turn.stop_reason}")

agent.hooks[AgentHook.AFTER_TURN] = [on_after_turn]
```

Hooks are not serialized in `to_dict()` / `from_dict()` for turns or agents.
