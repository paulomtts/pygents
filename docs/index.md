# pygents

A small Python library for **async tools**, **turns**, and **agents**. You define tools (coroutines or async generators), queue turns that invoke them, and run agents that process the queue and stream results. The run loop stops when a completion-check tool returns `True`.

- **Functional:** Define what should happen (turns), not how (tools). Stream results as they are produced. Use completion checks and inter-agent messaging to control flow.
- **Technical:** All tools are async. Turns enforce timeouts, optional per-tool locking, and immutability while running. Hooks at turn, agent, and tool level let you observe or intercept execution.

Continue with [Overview](overview.md) for capabilities, or [Quick start](quickstart.md) for a minimal example.
