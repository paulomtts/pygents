# Building an AI Agent

This guide walks through building a conversational AI agent that can manage a calendar. You'll learn how to:

- Define tools that call an LLM
- Use structured outputs with `response_model`
- Let tools control the agent's flow by returning `Turn` objects

## Tool-driven flow control

The key pattern in pygents is that **tools can return `Turn` objects**. When a tool returns a `Turn`, the agent automatically enqueues it for execution. This lets tools decide what happens next — not external orchestration code.

```python
@tool()
async def decide_next_step(context: str) -> str | Turn:
    if needs_more_info(context):
        return Turn("gather_info", kwargs={"context": context})
    return "Done processing"
```

This is powerful because:

- **Tools control flow** — the logic lives where the domain knowledge is
- **Chaining is implicit** — no explicit queue management needed
- **Conditional branching** — tools can choose different paths based on results

## The LLM tool

Define a tool that sends messages to an LLM. This example uses a generic `llm.chat` interface — adapt it to your provider (OpenAI, Anthropic, etc.).

```python
from pygents import tool, Turn

@tool()
async def chat(messages: list[dict], system: str | None = None) -> str | Turn:
    """Chat with the LLM. May return a Turn to trigger tool use."""
    response = await llm.chat(
        messages=messages,
        system=system,
    )
    return response.content
```

Note the return type: `str | Turn`. The tool can return a plain response, or it can return a `Turn` to trigger another action.

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
```

## The calendar tools

Now define tools that work with calendar events. Each tool can return a `Turn` to chain the next action:

```python
# In-memory calendar for this example
calendar: list[CalendarEvent] = []

@tool()
async def create_event(user_request: str) -> Turn:
    """Parse a user request into a calendar event, then save it."""
    event = await llm.chat(
        messages=[{"role": "user", "content": user_request}],
        system="Extract the calendar event from the user's message.",
        response_model=CalendarEvent,
    )
    # Return a Turn to chain into save_event
    return Turn("save_event", kwargs={"event": event})

@tool()
async def save_event(event: CalendarEvent) -> str:
    """Save an event to the calendar."""
    calendar.append(event)
    return f"Saved: {event.title} on {event.start.strftime('%Y-%m-%d %H:%M')}"
```

When `create_event` runs, it parses the user's request into a `CalendarEvent`, then returns a `Turn` that triggers `save_event`. The agent handles the chaining automatically.

## Adding decision logic

Tools can make conditional decisions about what to do next:

```python
@tool()
async def process_request(user_input: str, messages: list[dict]) -> str | Turn:
    """Decide how to handle the user's request."""
    # Ask the LLM to classify the intent
    intent = await llm.chat(
        messages=[{"role": "user", "content": user_input}],
        system="Classify the intent: 'schedule', 'query', or 'chat'. Return only the word.",
    )

    if intent.strip() == "schedule":
        # Chain to create_event
        return Turn("create_event", kwargs={"user_request": user_input})

    if intent.strip() == "query":
        # Chain to query_calendar
        return Turn("query_calendar", kwargs={"query": user_input})

    # Default: just chat
    messages.append({"role": "user", "content": user_input})
    response = await llm.chat(messages=messages)
    return response.content

@tool()
async def query_calendar(query: str) -> str:
    """Search the calendar and return matching events."""
    # Simple search for demo
    matches = [e for e in calendar if query.lower() in e.title.lower()]
    if not matches:
        return "No events found."
    return "\n".join(f"- {e.title} at {e.start}" for e in matches)
```

The `process_request` tool classifies the user's intent and returns different `Turn` objects to route the request appropriately.

## Wiring it together

Create an agent with all tools and run a conversation loop:

```python
from pygents import Agent, Turn

agent = Agent(
    name="assistant",
    description="A helpful assistant that manages your calendar",
    tools=[process_request, create_event, save_event, query_calendar],
)

async def conversation():
    messages = []

    while True:
        user_input = input("You: ")
        if user_input.lower() in ("quit", "exit"):
            break

        # Start with process_request — it decides what to do next
        await agent.put(Turn("process_request", kwargs={
            "user_input": user_input,
            "messages": messages,
        }))

        # Run until the queue is empty (all chained turns complete)
        async for turn, result in agent.run():
            if isinstance(result, str):
                print(f"Assistant: {result}")
                messages.append({"role": "assistant", "content": result})
```

The agent processes `process_request`, which may return a `Turn` for `create_event`, which returns a `Turn` for `save_event`. Each step chains automatically until a tool returns a plain value instead of a `Turn`.

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
async def process_request(user_input: str, messages: list[dict]) -> str | Turn:
    """Route the request based on intent."""
    intent = await llm.chat(
        messages=[{"role": "user", "content": user_input}],
        system="Classify: 'schedule', 'query', or 'chat'. Return only the word.",
    )

    if "schedule" in intent.lower():
        return Turn("create_event", kwargs={"user_request": user_input})
    if "query" in intent.lower():
        return Turn("query_calendar", kwargs={"query": user_input})

    messages.append({"role": "user", "content": user_input})
    return (await llm.chat(messages=messages)).content

@tool()
async def create_event(user_request: str) -> Turn:
    """Parse request and chain to save."""
    event = await llm.chat(
        messages=[{"role": "user", "content": user_request}],
        system="Extract the calendar event.",
        response_model=CalendarEvent,
    )
    return Turn("save_event", kwargs={"event": event})

@tool()
async def save_event(event: CalendarEvent) -> str:
    """Save and confirm."""
    calendar.append(event)
    return f"Saved: {event.title} on {event.start.strftime('%Y-%m-%d %H:%M')}"

@tool()
async def query_calendar(query: str) -> str:
    """Search calendar."""
    matches = [e for e in calendar if query.lower() in e.title.lower()]
    if not matches:
        return "No events found."
    return "\n".join(f"- {e.title} at {e.start}" for e in matches)

agent = Agent("assistant", "Calendar assistant", [
    process_request, create_event, save_event, query_calendar
])

async def main():
    await agent.put(Turn("process_request", kwargs={
        "user_input": "Schedule a team standup tomorrow at 9am for 30 minutes",
        "messages": [],
    }))

    async for turn, result in agent.run():
        print(f"[{turn.tool_name}] {result}")

asyncio.run(main())
```

This pattern — **tools return `Turn` objects to control flow** — is the foundation of agentic systems. The agent handles queuing, streaming, and error handling; you focus on the decision logic inside each tool.
