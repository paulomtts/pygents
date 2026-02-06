## Design decisions

**Tools are always async.** The `@tool` decorator accepts only coroutine functions or async generator functions; sync `def` is rejected at decoration time. Single-value tools use `returning()`; streaming tools (async generators) use `yielding()`. Using the wrong run method (e.g. `returning()` on an async gen tool) raises `WrongRunMethodError`.

**Turn reentrancy.** A Turn cannot be run again while it is already running. A second call to `returning()` or `yielding()` on the same Turn raises `SafeExecutionError`. Most Turn attributes are immutable while running; only `_is_running`, `start_time`, `end_time`, `output`, `stop_reason`, and `metadata` may change during execution.

**Per-tool locking, default off.** Turns run tools that may or may not touch shared state. Locking is per-tool and opt-in: `@tool(lock=True)` serializes concurrent runs of that tool; the default is no lock so stateless or non-conflicting tools can run in parallel. Tools that manipulate shared resources should set `lock=True`.

**Timeouts.** Each Turn has a `timeout` (default 60s). If execution exceeds it, the run raises `TurnTimeoutError`, `stop_reason` is set to `StopReason.TIMEOUT`, and `end_time` is recorded. For `returning()` the timeout applies to the single await; for `yielding()` it applies to the whole run (streaming is bounded by the same deadline).

**Tool registry.** Tools register by name when decorated. A Turn is bound to a tool by name and kwargs at construction; the tool is resolved from the registry at init. Duplicate tool names at registration raise `ValueError`.

**Agents stream by default.** An Agent's `run()` is an async generator that yields `(turn, value)` for each result as it is produced. Single-value tools yield once; async-generator tools yield per value. Consume with `async for turn, value in agent.run(): ...`. The loop ends when a completion-check tool returns `True`.

**Tool arguments by reference are evaluated at runtime.** When constructing a Turn, any argument value that is a no-arg function (e.g. a lambda or a callable with no required parameters) is not resolved at Turn creation. It is evaluated at the moment the tool is invoked (in `returning()` or `yielding()`), and the result is passed to the tool. Non-callable values are passed through unchanged. This allows dynamic values (e.g. reading from memory or environment) to be resolved when the Turn runs.

**COMPLETION_CHECK tools must return bool.** A tool declared with `type=ToolType.COMPLETION_CHECK` must be a coroutine (not an async generator) with return annotation `-> bool`. The decorator enforces this at registration time. When the agent checks whether to stop the run loop, it validates that the turn's output is actually a bool and raises `CompletionCheckReturnError` otherwise. The `CompletionCheckTool` protocol in `app.tool` describes the type contract for static checkers.

**Turn metadata and serialization.** A Turn accepts an optional `metadata` dict for arbitrary key-value data; it is mutable (including while the turn is running). Turns can be serialized to a dict with `to_dict()` and restored with `Turn.from_dict(data)`. The serialized form includes `uuid`, `tool_name`, `kwargs`, `metadata`, `timeout`, `start_time`, `end_time`, `stop_reason`, and `output`; datetimes are ISO strings. On restore, the tool is resolved from the registry by `tool_name`. Hooks are not serialized.

**Agent serialization.** Agents support `to_dict()` and `Agent.from_dict(data)` for persistence. The dict contains `name`, `description`, `tool_names` (list of registered tool names), and `queue` (list of turn dicts from each queued turn). On restore, tools are resolved from the registry by name and the queue is repopulated with `Turn.from_dict()`; the agent is registered in `AgentRegistry`. Hooks are not serialized. Queue snapshot uses the public get/put API so order is preserved.

## Hooks

Hooks are async callbacks you can register to observe or intercept at three levels. All hooks are awaited; exceptions in a hook propagate. Hook names are enums: `TurnHook`, `AgentHook`, `ToolHook` (from `app.hooks`).

| Level | Enum | When |
|-------|------|------|
| **Turn** | `TurnHook.BEFORE_RUN` | Before `returning()` or `yielding()` runs (after lock acquired). Args: `(turn)`. |
| **Turn** | `TurnHook.AFTER_RUN` | After run completes successfully. Args: `(turn)`. |
| **Turn** | `TurnHook.ON_TIMEOUT` | When the turn times out. Args: `(turn)`. |
| **Turn** | `TurnHook.ON_ERROR` | When the tool raises (non-timeout). Args: `(turn, exception)`. |
| **Turn** | `TurnHook.ON_VALUE` | For yielding tools only: before each value is yielded. Args: `(turn, value)`. |
| **Agent** | `AgentHook.BEFORE_TURN` | Before popping a turn from the queue. Args: `(agent)`. |
| **Agent** | `AgentHook.AFTER_TURN` | After a turn is fully processed (before next pop or exit). Args: `(agent, turn)`. |
| **Agent** | `AgentHook.ON_TURN_VALUE` | For each streamed `(turn, value)` before it is yielded. Args: `(agent, turn, value)`. |
| **Agent** | `AgentHook.ON_TURN_ERROR` | When a turn raises. Args: `(agent, turn, exception)`. |
| **Agent** | `AgentHook.ON_TURN_TIMEOUT` | When a turn times out. Args: `(agent, turn)`. |
| **Agent** | `AgentHook.BEFORE_PUT` | Before putting a turn on the queue. Args: `(agent, turn)`. |
| **Agent** | `AgentHook.AFTER_PUT` | After putting a turn on the queue. Args: `(agent, turn)`. |
| **Tool** | `ToolHook.BEFORE_INVOKE` | When a turn is about to call the tool (inside Turn run). Args: `(turn, kwargs)`. |
| **Tool** | `ToolHook.AFTER_INVOKE` | After the tool returns a value (single-value: once; yielding: per value). Args: `(turn, value)`. |

Register by enum: `turn.hooks[TurnHook.BEFORE_RUN] = [my_async_fn]`, `agent.hooks[AgentHook.AFTER_TURN] = [...]`, `tool.hooks[ToolHook.BEFORE_INVOKE] = [...]`.
