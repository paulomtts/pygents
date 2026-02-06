# Design tables

Summary of conditions and outcomes for tools, turns, and agents. See the source and [Design decisions](design-decisions.md) for full detail.

## Tools

**Validation at decoration**

| Function is async coroutine | Function is async gen | type is COMPLETION_CHECK | COMPLETION_CHECK return bool | Result |
|----------------------------|------------------------|---------------------------|------------------------------|--------|
| ✓ | ✗ | ✗ | — | Valid (single-value) |
| ✓ | ✗ | ✓ | ✓ | Valid (completion check) |
| ✗ | ✓ | ✗ | — | Valid (streaming) |
| ✓ | ✗ | ✓ | ✗ | TypeError |

**Run method (Turn)**

| Tool is coroutine | Tool is async gen | Turn method |
|-------------------|-------------------|-------------|
| ✓ | ✗ | `returning()` |
| ✗ | ✓ | `yielding()` |

**Registry:** Duplicate name → `ValueError`. `get(name)` missing → `UnregisteredToolError`.

---

## Turns

**Run method choice**

| Tool implementation | `returning()` | `yielding()` |
|---------------------|---------------|--------------|
| Coroutine | ✓ | WrongRunMethodError |
| Async generator | WrongRunMethodError | ✓ |

**Mutable while running:** Only `_is_running`, `start_time`, `end_time`, `output`, `stop_reason`, `metadata`. Others → `SafeExecutionError`.

**Outcome**

| Event | stop_reason | Exception |
|-------|-------------|-----------|
| Success | COMPLETED | — |
| Timeout | TIMEOUT | TurnTimeoutError |
| Tool/hook raises | ERROR | re-raised |

---

## Agents

**put(turn)**

| turn.tool is None | tool name not in agent's tools | Result |
|-------------------|--------------------------------|--------|
| ✓ | — | ValueError |
| ✗ | ✓ | ValueError |
| ✗ | ✗ | Enqueue, run BEFORE_PUT / AFTER_PUT |

**Run loop exit**

| After turn | Next |
|------------|------|
| COMPLETION_CHECK and output is True | Exit loop |
| COMPLETION_CHECK and output is not bool | CompletionCheckReturnError |
| output is a Turn instance | put(output), then next iteration |
| Else | Next iteration |

**Run while already running:** `SafeExecutionError`.
