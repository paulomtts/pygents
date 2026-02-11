# Building an AI Agent

This guide builds a small **calendar assistant** using **pygents** for orchestration, [py-ai-toolkit](https://paulomtts.github.io/py-ai-toolkit/) for LLM calls, and **Memory** for conversation context. You get:

- Three tools: `think` (chooses what to do), `respond` (replies to the user), and `create_event` (saves an event and chains back to think)
- A bounded **memory** of recent messages; the **user message is appended to memory as soon as it arrives** in the loop (before the turn runs), and every tool receives only **memory**
- Tools that return `Turn` objects so the agent runs the next step automatically

The code blocks below are meant to be combined into one script in order.

## Install

```bash
uv add pygents py-ai-toolkit
```

Configure the LLM (e.g. via env vars) and create the toolkit and memory:

```python
import logging

from py_ai_toolkit import PyAIToolkit
from py_ai_toolkit.core.domain.interfaces import LLMConfig

from pygents import Memory, hook, AgentHook

logger = logging.getLogger(__name__)

# Uses LLM_MODEL, LLM_API_KEY, etc. from the environment if set
toolkit = PyAIToolkit(main_model_config=LLMConfig())

# Last 20 messages kept as context for the LLM
memory = Memory(limit=20)

@hook(AgentHook.AFTER_TURN)
async def log_turn(agent, turn):
    logger.debug("%s: %s → %s", agent.name, turn.tool.metadata.name, turn.stop_reason)
```

!!! info "LLM configuration"
    `LLMConfig()` with no arguments reads from the environment. Set `LLM_MODEL`, `LLM_API_KEY`, and optionally `LLM_BASE_URL` (see [py-ai-toolkit Getting Started](https://paulomtts.github.io/py-ai-toolkit/getting-started/)).

## Appending the user message when it arrives

When a user message enters the loop, append it to memory **before** enqueueing the turn. That way every tool only needs `memory`; the latest user message is already in memory when `think` runs. When chaining back to `think` (e.g. after creating an event), the tool that queues the next `think` turn appends the new “user” message to memory first, then returns the turn.

!!! warning "Append before enqueueing"
    If you enqueue a `think` turn without appending a user message to memory first, the model will see empty or stale context.

## The think tool

`think` only **chooses** what to do next. It reads recent context from memory and uses `asend()` with a response model that has a single field: **respond** or **create_event**. It then queues the right tool; those tools do the actual work.

```python
from typing import Literal

from pydantic import BaseModel

from pygents import tool, Turn

class ThinkResponse(BaseModel):
    """Choice only: reply to the user or create an event. The chosen tool does the rest."""
    action: Literal["respond", "create_event"]

@tool()
async def think(memory: Memory) -> Turn:
    """Choose how to respond: queue respond or create_event."""
    context = "\n".join(memory.items[-10:])
    response = await toolkit.asend(
        response_model=ThinkResponse,
        template="You are a calendar assistant. Recent conversation:\n{{ context }}\n\n"
                 "Choose 'respond' to reply to the user, or 'create_event' if they want to schedule something.",
        context=context,
    )

    if response.content.action == "create_event":
        return Turn(create_event, kwargs=dict(memory=memory))
    return Turn(respond, kwargs=dict(memory=memory))
```

Define all three tools in the same module; `think` can reference `respond` and `create_event` because the turn is only created when `think` runs. When a tool returns a `Turn`, the agent enqueues it and runs it next.

## The respond tool

`respond` generates a reply from the LLM using the recent conversation in memory, appends the assistant message to memory, and returns the reply text.

```python
@tool()
async def respond(memory: Memory) -> str:
    """Generate a reply from recent context and append it to memory."""
    context = "\n".join(memory.items[-10:])
    reply_response = await toolkit.asend(
        response_model=ReplyText,
        template="You are a calendar assistant. Recent conversation:\n{{ context }}\n\nReply briefly.",
        context=context,
    )
    text = reply_response.content.reply
    await memory.append(f"Assistant: {text}")
    return text

class ReplyText(BaseModel):
    reply: str
```

## The calendar tool

`create_event` reads the latest user message from memory — the most recent line that starts with `"User: "` — and extracts a structured event with `asend()`. If there is no such line, `_last_user_message` returns an empty string. It then appends the event to the calendar and a summary to memory, appends the follow-up user message, and queues **think**.

```python
from datetime import datetime

class CalendarEvent(BaseModel):
    title: str
    start: datetime
    end: datetime

calendar: list[CalendarEvent] = []

def _last_user_message(memory: Memory) -> str:
    for item in reversed(memory.items):
        if isinstance(item, str) and item.startswith("User:"):
            return item.removeprefix("User:").strip()
    return ""

@tool()
async def create_event(memory: Memory) -> Turn:
    """Extract and save a calendar event from the last user message, then chain back to think."""
    user_message = _last_user_message(memory)
    response = await toolkit.asend(
        response_model=CalendarEvent,
        template="Extract a calendar event from this request: {{ user_message }}. "
                 "Use reasonable defaults for missing date/time.",
        user_message=user_message,
    )
    event = response.content
    calendar.append(event)

    summary = f"Created '{event.title}' on {event.start:%Y-%m-%d %H:%M}"
    await memory.append(f"Assistant: {summary}")

    await memory.append("User: Confirm the event was created.")
    return Turn(think, kwargs=dict(memory=memory))
```

## Putting it together

Append the first user message to memory, then enqueue a **think** turn with only `memory`. Run the agent.

```python
import asyncio
from pygents import Agent, Turn

agent = Agent("assistant", "Calendar assistant", [think, respond, create_event])
agent.memory = memory
agent.hooks.append(log_turn)

# Set logging to DEBUG to see turn completion in logs, e.g.:
# logging.basicConfig(level=logging.DEBUG)

async def main():
    await memory.append("User: Schedule standup tomorrow at 9am")
    await agent.put(Turn(think, kwargs=dict(memory=memory)))

    async for turn, result in agent.run():
        if isinstance(result, str):
            print(f"[assistant] {result}")

asyncio.run(main())
```

Flow: **User message appended** → **think** runs (reads memory) → LLM returns `action=create_event` → **create_event** runs (reads last user message from memory, extracts event, saves, appends assistant summary, appends “User: Confirm…”) → **think** runs again → LLM returns `action=respond` → **respond** runs (generates reply from memory, appends to memory) → returns string → done.

## Summary

| Piece | Role |
|-------|------|
| **User message** | Appended to memory as soon as it arrives — before `put()` for the first message, or before returning `Turn(think, ...)` when chaining. No tool takes a chat/user message argument. |
| **think** | Reads memory, chooses action, queues `respond` or `create_event` with `memory`. |
| **respond** | Reads memory, generates reply via LLM, appends to memory, returns reply text. |
| **create_event** | Reads last user message from memory, extracts event, saves, appends to memory, appends follow-up user message, queues think with `memory`. |
| **Memory** | Single source of context; all tools receive only `memory`. |

For more, see [Tools](concepts/tools.md), [Turns](concepts/turns.md), [Agents](concepts/agents.md), and [Memory](concepts/memory.md).
