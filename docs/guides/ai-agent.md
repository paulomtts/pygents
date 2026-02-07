# Building an AI Agent

This guide builds a calendar assistant with two tools and one loop:

- `chat` — talks to the LLM, returns `str` or a `Turn` to `create_event`
- `create_event` — saves an event, always returns a `Turn` back to `chat`

## The chat tool

```python
from pygents import tool, Turn

@tool()
async def chat(messages: list[dict]) -> str | Turn:
    """Talk to the LLM. Chains to create_event when scheduling is needed."""
    response = await llm.chat(messages=messages, system="You are a calendar assistant.")

    if response.tool_calls:  # LLM wants to create an event
        return Turn(create_event, kwargs=dict(
            request=response.tool_calls[0].arguments["request"],
            messages=messages,
        ))

    return response.content
```

The return type `str | Turn` is the key. When a tool returns a `Turn`, the agent enqueues it automatically. When it returns a plain value, the chain stops.

## The calendar tool

```python
from pydantic import BaseModel
from datetime import datetime

class CalendarEvent(BaseModel):
    title: str
    start: datetime
    end: datetime

calendar: list[CalendarEvent] = []

@tool()
async def create_event(request: str, messages: list[dict]) -> Turn:
    """Parse and save a calendar event, then chain back to chat."""
    event = await llm.chat(
        messages=[dict(role="user", content=request)],
        system="Extract the calendar event from the user's message.",
        response_model=CalendarEvent,
    )
    calendar.append(event)

    messages.append(dict(
        role="assistant",
        content=f"Created '{event.title}' on {event.start:%Y-%m-%d %H:%M}",
    ))
    return Turn(chat, kwargs=dict(messages=messages))
```

`create_event` always returns a `Turn` back to `chat`. This closes the loop — the agent confirms the event to the user on the next chat turn.

## Putting it together

```python
import asyncio
from pygents import Agent, Turn

agent = Agent("assistant", "Calendar assistant", [chat, create_event])

async def main():
    messages = [dict(role="user", content="Schedule standup tomorrow at 9am")]
    await agent.put(Turn(chat, kwargs=dict(messages=messages)))

    async for turn, result in agent.run():
        print(f"[{turn.tool.metadata.name}] {result}")

asyncio.run(main())
```

The flow: `chat` → detects scheduling intent → `Turn(create_event)` → saves event → `Turn(chat)` → LLM confirms → returns `str` → done.
