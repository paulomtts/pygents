# Building an AI Agent

This guide walks through building a conversational AI agent that can manage a calendar. You'll learn how to:

- Define tools that call an LLM
- Use structured outputs with `response_model`
- Chain tools together in a conversation loop

## The LLM tool

First, define a tool that sends messages to an LLM. This example uses a generic `llm.chat` interface — adapt it to your provider (OpenAI, Anthropic, etc.).

```python
from pygents import tool

@tool()
async def chat(messages: list[dict], system: str | None = None) -> str:
    """Send messages to the LLM and return the response."""
    response = await llm.chat(
        messages=messages,
        system=system,
    )
    return response.content
```

The tool takes a conversation history and optional system prompt, returning the assistant's reply.

## Structured outputs with response_model

For the calendar, we want the LLM to return structured data — not free text. Define a Pydantic model and pass it as `response_model`:

```python
from pydantic import BaseModel
from datetime import datetime

class CalendarEvent(BaseModel):
    title: str
    start: datetime
    end: datetime
    description: str | None = None

@tool()
async def create_event(user_request: str) -> CalendarEvent:
    """Parse a user request into a calendar event."""
    response = await llm.chat(
        messages=[{"role": "user", "content": user_request}],
        system="Extract the calendar event from the user's message. Return structured data.",
        response_model=CalendarEvent,
    )
    return response  # Already a CalendarEvent instance
```

The LLM returns a validated `CalendarEvent` object, not raw text. This makes downstream processing reliable.

## The calendar tool

Now add a tool that saves events to a calendar:

```python
# In-memory calendar for this example
calendar: list[CalendarEvent] = []

@tool()
async def save_event(event: CalendarEvent) -> str:
    """Save an event to the calendar."""
    calendar.append(event)
    return f"Saved: {event.title} on {event.start.strftime('%Y-%m-%d %H:%M')}"
```

## Wiring it together

Create an agent with all tools and run a conversation loop:

```python
from pygents import Agent, Turn

agent = Agent(
    name="assistant",
    description="A helpful assistant that manages your calendar",
    tools=[chat, create_event, save_event],
)

async def conversation():
    messages = []
    system = """You are a helpful assistant. When the user wants to schedule something,
    use create_event to parse their request, then save_event to store it."""

    while True:
        user_input = input("You: ")
        if user_input.lower() in ("quit", "exit"):
            break

        messages.append({"role": "user", "content": user_input})

        # Get LLM response
        await agent.put(Turn("chat", kwargs={
            "messages": messages,
            "system": system,
        }))

        async for turn, reply in agent.run():
            print(f"Assistant: {reply}")
            messages.append({"role": "assistant", "content": reply})
```

## Adding tool use

To let the LLM decide when to create events, extend the chat tool to handle tool calls:

```python
@tool()
async def chat_with_tools(messages: list[dict], system: str | None = None) -> str | Turn:
    """Chat with the LLM, returning either a reply or a tool call."""
    response = await llm.chat(
        messages=messages,
        system=system,
        tools=[
            {
                "name": "create_event",
                "description": "Parse a user request into a calendar event",
                "parameters": CalendarEvent.model_json_schema(),
            }
        ],
    )

    if response.tool_calls:
        # LLM wants to call a tool — return a Turn to chain it
        tool_call = response.tool_calls[0]
        return Turn("create_event", kwargs={"user_request": tool_call.arguments})

    return response.content
```

When the tool returns a `Turn`, the agent automatically enqueues it. This chains the LLM's decision into the next action.

## Full example

```python
import asyncio
from datetime import datetime
from pydantic import BaseModel
from pygents import Agent, Turn, tool

class CalendarEvent(BaseModel):
    title: str
    start: datetime
    end: datetime
    description: str | None = None

calendar: list[CalendarEvent] = []

@tool()
async def chat(messages: list[dict], system: str | None = None) -> str:
    response = await llm.chat(messages=messages, system=system)
    return response.content

@tool()
async def create_event(user_request: str) -> CalendarEvent:
    response = await llm.chat(
        messages=[{"role": "user", "content": user_request}],
        system="Extract the calendar event. Return structured data.",
        response_model=CalendarEvent,
    )
    return response

@tool()
async def save_event(event: CalendarEvent) -> str:
    calendar.append(event)
    return f"Saved: {event.title}"

agent = Agent("assistant", "Calendar assistant", [chat, create_event, save_event])

async def main():
    # Schedule a meeting
    await agent.put(Turn("create_event", kwargs={
        "user_request": "Schedule a team standup tomorrow at 9am for 30 minutes"
    }))

    async for turn, result in agent.run():
        if isinstance(result, CalendarEvent):
            # Chain: save the event
            await agent.put(Turn("save_event", kwargs={"event": result}))
        else:
            print(result)

asyncio.run(main())
```

This pattern — LLM decides, tools execute, results chain — is the foundation of agentic systems. The agent handles queuing, streaming, and error handling; you focus on the tools.
