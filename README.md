## Design decisions

**Tools are always async.** The `@tool` decorator accepts only coroutine functions or async generator functions; sync `def` is rejected at decoration time. Single-value tools use `returning()`; streaming tools (async generators) use `yielding()`. Using the wrong run method (e.g. `returning()` on an async gen tool) raises `WrongRunMethodError`.

**Turn reentrancy.** A Turn cannot be run again while it is already running. A second call to `returning()` or `yielding()` on the same Turn raises `SafeExecutionError`. Most Turn attributes are immutable while running; only `_is_running`, `start_time`, `end_time`, `output`, and `stop_reason` may change during execution.

**Per-tool locking, default off.** Turns run tools that may or may not touch shared state. Locking is per-tool and opt-in: `@tool(lock=True)` serializes concurrent runs of that tool; the default is no lock so stateless or non-conflicting tools can run in parallel. Tools that manipulate shared resources should set `lock=True`.

**Timeouts.** Each Turn has a `timeout` (default 60s). If execution exceeds it, the run raises `TurnTimeoutError`, `stop_reason` is set to `StopReason.TIMEOUT`, and `end_time` is recorded. For `returning()` the timeout applies to the single await; for `yielding()` it applies to the whole run (streaming is bounded by the same deadline).

**Tool registry.** Tools register by name when decorated. A Turn is bound to a tool by name and kwargs at construction; the tool is resolved from the registry at init. Duplicate tool names at registration raise `ValueError`.

**Agents stream by default.** An Agent's `run()` is an async generator that yields `(turn, value)` for each result as it is produced. Single-value tools yield once; async-generator tools yield per value. Consume with `async for turn, value in agent.run(): ...`. The loop ends when a completion-check tool returns `True`.

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

## Roadmap

1. **Agent registry** — Central registry for Agents by name (analogous to ToolRegistry), so Agents can be looked up and composed (e.g. “send this turn to agent X”).

2. **Agent send turn to other agent** — Ability for one Agent to enqueue a Turn on another Agent’s queue. May require Turns to be owned (e.g. by an Agent or session) so routing and lifecycle are well-defined.

3. **Protocols for different ToolTypes** — Formal protocols or contracts per `ToolType` (e.g. COMPLETION_CHECK returns bool, MEMORY_READ/​MEMORY_WRITE signatures, REASONING vs ACTION semantics) so tool implementations and agents can rely on consistent shapes and behavior.

4. **Turn and agent serialization** — Serialization of turns and agents for storing and restoring state (e.g. to disk or a store), so conversation and agent state can be persisted and resumed later.