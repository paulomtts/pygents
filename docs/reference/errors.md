# Errors

All are in `pygents.errors` and re-exported from `pygents`.

| Exception | Base | When |
|-----------|------|------|
| `SafeExecutionError` | Exception | Calling `returning()`/`yielding()` while the turn is already running; or changing a turn/agent attribute that is immutable while running. |
| `WrongRunMethodError` | Exception | Using `returning()` on an async-generator tool or `yielding()` on a coroutine tool. |
| `TurnTimeoutError` | TimeoutError | Turn execution exceeded its `timeout`. |
| `UnregisteredToolError` | KeyError | `ToolRegistry.get(name)` with no tool for that name. |
| `UnregisteredAgentError` | KeyError | `AgentRegistry.get(name)` with no agent for that name. |

`ValueError` is used for: duplicate tool or agent name at registration; agent built with a tool instance that is not the same as the one in the registry; `put(turn)` when the turn has no tool or the tool is not in the agent's set.
