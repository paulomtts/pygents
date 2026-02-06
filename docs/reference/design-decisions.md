# Design decisions

**Tools are always async.** Sync `def` is rejected at decoration time. This keeps the execution model uniform — no mixed sync/async paths.

**Turn reentrancy is blocked.** A turn cannot run twice simultaneously. Most attributes are immutable while running. This prevents race conditions from accidental reuse.

**Per-tool locking is opt-in.** `@tool(lock=True)` serializes concurrent runs. Default is no lock, so stateless tools run in parallel without contention.

**Every turn has a timeout.** Default 60s. Applies to the single await for `returning()`, or the entire run for `yielding()`. Prevents unbounded execution.

**Tools register globally.** The `@tool` decorator registers by function name. Turns resolve tools from the registry at construction time. Duplicate names are rejected.

**Agents stream by default.** `run()` is an async generator yielding `(turn, value)` as results are produced — not batched. Completion checks end the loop.

**Callable kwargs are late-evaluated.** Any no-arg callable passed as a kwarg is called at tool invocation time, not at turn creation. This supports dynamic config, tokens, memory reads, etc.

**COMPLETION_CHECK tools must return bool.** Enforced at decoration time (return annotation) and at runtime (agent validates output). Prevents ambiguous completion signals.

**Hooks are runtime-only.** Not included in serialization. They are implementation concerns, not data.
