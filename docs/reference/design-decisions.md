# Design decisions

**Tools are always async.** The `@tool` decorator accepts only coroutine functions or async generator functions; sync `def` is rejected at decoration time. Single-value tools use `returning()`; streaming tools (async generators) use `yielding()`. Using the wrong run method raises `WrongRunMethodError`.

**Turn reentrancy.** A turn cannot be run again while it is already running. A second call to `returning()` or `yielding()` on the same turn raises `SafeExecutionError`. Most turn attributes are immutable while running; only `_is_running`, `start_time`, `end_time`, `output`, `stop_reason`, and `metadata` may change during execution.

**Per-tool locking, default off.** Locking is per-tool and opt-in: `@tool(lock=True)` serializes concurrent runs of that tool; the default is no lock so stateless or non-conflicting tools can run in parallel. Tools that manipulate shared resources should set `lock=True`.

**Timeouts.** Each turn has a `timeout` (default 60s). If execution exceeds it, the run raises `TurnTimeoutError`, `stop_reason` is set to `StopReason.TIMEOUT`, and `end_time` is recorded. For `returning()` the timeout applies to the single await; for `yielding()` it applies to the whole run.

**Tool registry.** Tools register by name when decorated. A turn is bound to a tool by name and kwargs at construction; the tool is resolved from the registry at init. Duplicate tool names at registration raise `ValueError`.

**Agents stream by default.** An agent's `run()` is an async generator that yields `(turn, value)` for each result as it is produced. Single-value tools yield once; async-generator tools yield per value. Consume with `async for turn, value in agent.run(): ...`. The loop ends when a completion-check tool returns `True`.

**Tool arguments by reference are evaluated at runtime.** When constructing a turn, any kwarg value that is a no-arg callable is not resolved at turn creation. It is evaluated when the tool is invoked, and the result is passed to the tool. Non-callable values are passed through unchanged. This allows dynamic values (e.g. from memory or environment) to be resolved when the turn runs.

**COMPLETION_CHECK tools must return bool.** A tool declared with `type=ToolType.COMPLETION_CHECK` must be a coroutine (not an async generator) with return annotation `-> bool`. The decorator enforces this at registration. When the agent checks whether to stop, it validates that the turn's output is a bool and raises `CompletionCheckReturnError` otherwise.

**Turn metadata and serialization.** A turn accepts an optional `metadata` dict; it is mutable (including while running). Turns serialize with `to_dict()` and restore with `Turn.from_dict(data)`. Serialized form includes `uuid`, `tool_name`, `kwargs`, `metadata`, `timeout`, `start_time`, `end_time`, `stop_reason`, `output`; datetimes are ISO strings. Hooks are not serialized.

**Agent serialization.** Agents support `to_dict()` and `Agent.from_dict(data)`. The dict contains `name`, `description`, `tool_names`, and `queue` (list of turn dicts). On restore, tools are resolved from the registry and the queue is repopulated; the agent is registered. Hooks are not serialized.
