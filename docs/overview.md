# Overview

## What it does

**Tools** are async functions (or async generators) that perform a unit of work. They are registered by name when decorated with `@tool`.

**Turns** are work items: a tool name plus arguments. You create a turn, then run it with `returning()` for a single result or `yielding()` for a stream. The turn resolves the tool from the registry and runs it with optional timeout and locking.

**Agents** own a queue of turns and a set of tools. `run()` is an async generator: it pops turns, runs them, and yields `(turn, value)` for each result. Single-value tools yield once; streaming tools yield per item. The loop stops when a turn uses a **completion-check** tool that returns `True`. A turn can also enqueue another turn (e.g. for sub-tasks or handoff to another agent).

## Main capabilities

| Capability | Description |
|------------|-------------|
| **Streaming** | Agents yield results as they are produced. Use `async for turn, value in agent.run(): ...`. |
| **Completion checks** | A tool with `type=ToolType.COMPLETION_CHECK` and return type `bool` signals "done" when it returns `True`; the agent then exits the run loop. |
| **Inter-agent messaging** | `agent.send_turn(other_agent_name, turn)` enqueues a turn on another registered agent. |
| **Dynamic arguments** | Turn kwargs can be callables (e.g. `lambda: get_config()`); they are evaluated when the tool runs, not when the turn is created. |
| **Timeouts** | Each turn has a `timeout` (default 60s). Exceeding it sets `stop_reason=TIMEOUT` and raises `TurnTimeoutError`. |
| **Per-tool locking** | `@tool(lock=True)` serializes concurrent runs of that tool; useful for shared state. |
| **Hooks** | Turn, agent, and tool hooks let you run async callbacks before/after runs, on timeout, on error, or per streamed value. |
| **Serialization** | `Turn.to_dict()` / `Turn.from_dict()` and `Agent.to_dict()` / `Agent.from_dict()` for persistence; hooks are not serialized. |

Next: [Quick start](quickstart.md) or [Concepts](concepts/tools.md).
