# Building a Research Assistant

This guide builds a **research assistant** that accumulates documents in a `ContextPool` and answers questions by querying the pool with the LLM — presenting lightweight descriptions to decide which items are relevant, then injecting only those contents into the answer prompt.

It covers all five pygents abstractions working together in one coherent example:

| Pattern | Where |
|---------|-------|
| Tool-driven flow — tools return `Turn` objects | `think`, `select_context` |
| `ContextQueue` for conversation memory | All reading tools via context injection |
| `ContextPool` for document accumulation | `fetch_document` returns items; the agent stores them |
| Context injection — typed parameters, no manual wiring | `pool: ContextPool`, `memory: ContextQueue` in every tool |
| LLM-driven selective retrieval | `select_context` sends descriptions only; `answer` reads content |

The code blocks below are meant to be combined into one script.

??? example "Full implementation"

    ```python
    import asyncio
    import logging

    from py_ai_toolkit import PyAIToolkit
    from py_ai_toolkit.core.domain.interfaces import LLMConfig
    from pydantic import BaseModel

    from pygents import Agent, ContextPool, ContextQueue, ContextQueueHook, ToolHook, Turn, hook, tool
    from pygents.context import ContextItem

    logger = logging.getLogger(__name__)
    toolkit = PyAIToolkit(main_model_config=LLMConfig())

    @hook(ContextQueueHook.AFTER_APPEND)
    async def log_memory(items: list) -> None:
        logger.debug("ContextQueue: %d items", len(items))

    memory = ContextQueue(limit=30, hooks=[log_memory])

    DOCUMENTS = {
        "q3-earnings": {
            "description": "Q3 earnings report — revenue $42M (+18% YoY), gross margin 68%, guidance raised",
            "body": "Revenue: $42M. Gross margin: 68%. Net income: $8.2M. "
                    "Guidance for Q4: $46–48M. Key driver: enterprise tier growth (+34%).",
        },
        "product-roadmap": {
            "description": "2025 product roadmap — three new integrations, mobile app beta, AI features",
            "body": "Q1: Slack and Jira integrations. Q2: Mobile app public beta. "
                    "Q3: AI-assisted workflows. Q4: Enterprise audit logs.",
        },
        "incident-2024-11": {
            "description": "Incident report Nov 2024 — 47-minute outage, root cause: DB connection pool exhaustion",
            "body": "Duration: 47 min. Impact: 12% of API requests failed. "
                    "Root cause: connection pool limit hit during traffic spike. "
                    "Fix: pool size doubled; circuit breaker added.",
        },
        "hiring-plan": {
            "description": "2025 hiring plan — 8 engineers, 3 sales, 2 support; budget $3.2M",
            "body": "Engineering: 4 backend, 2 frontend, 1 ML, 1 DevOps. "
                    "Sales: 2 AEs, 1 SDR. Support: 2 CSMs. Total budget: $3.2M.",
        },
        "security-audit": {
            "description": "Security audit results — SOC 2 Type II passed, 3 low-severity findings",
            "body": "Result: SOC 2 Type II certified. Findings: (1) session tokens not rotated on role change — fixed; "
                    "(2) verbose error messages in staging — fixed; (3) MFA not enforced for API keys — in progress.",
        },
    }

    class ContextSelection(BaseModel):
        """IDs of the pooled items directly relevant to the question."""
        relevant_ids: list[str]

    class Answer(BaseModel):
        """Final answer to the user's question."""
        answer: str

    @tool()
    async def fetch_document(doc_id: str) -> ContextItem:
        """Fetch a document. The agent stores it in the context pool."""
        doc = DOCUMENTS.get(doc_id)
        if doc is None:
            raise ValueError(f"Unknown document: {doc_id!r}")
        return ContextItem(
            id=doc_id,
            description=doc["description"],
            content=doc["body"],
        )

    def latest_user_message(memory: ContextQueue) -> str:
        for item in reversed(memory.items):
            if isinstance(item.content, str) and item.content.startswith("User:"):
                return item.content.removeprefix("User:").strip()
        return ""

    @hook(ToolHook.BEFORE_INVOKE)
    async def log_pool_state(**kwargs) -> None:
        pool = kwargs.get("pool")
        if pool:
            logger.debug("think: pool has %d items\n%s", len(pool), pool.catalogue())

    @tool(hooks=[log_pool_state])
    async def think(pool: ContextPool, memory: ContextQueue) -> Turn:
        """Check if the pool has context; route to select_context or answer directly."""
        if not pool:
            return Turn(answer, kwargs={"relevant_ids": []})
        return Turn(select_context)

    @tool()
    async def select_context(pool: ContextPool, memory: ContextQueue) -> Turn:
        """Send descriptions to the LLM, collect relevant ids, hand off to answer."""
        question = latest_user_message(memory)
        response = await toolkit.asend(
            response_model=ContextSelection,
            template=(
                "Question: {{ question }}\n\n"
                "Available context items (id + one-line description only):\n{{ catalogue }}\n\n"
                "Return only the IDs of items directly relevant to answering this question."
            ),
            question=question,
            catalogue=pool.catalogue(),
        )
        return Turn(answer, kwargs={"relevant_ids": response.content.relevant_ids})

    @tool()
    async def answer(pool: ContextPool, memory: ContextQueue, relevant_ids: list[str]) -> str:
        """Read selected item content from the pool and generate the final answer."""
        question = latest_user_message(memory)
        pool_ids = {item.id for item in pool.items}
        selected = [pool.get(id) for id in relevant_ids if id in pool_ids]

        if selected:
            context_block = "\n\n".join(
                f"[{item.id}] {item.description}\n{item.content}"
                for item in selected
            )
        else:
            context_block = "(no relevant context found)"

        response = await toolkit.asend(
            response_model=Answer,
            template=(
                "Answer this question using only the context below. "
                "Be concise and cite the source id.\n\n"
                "Question: {{ question }}\n\n"
                "Context:\n{{ context_block }}"
            ),
            question=question,
            context_block=context_block,
        )
        text = response.content.answer
        await memory.append(ContextItem(content=f"Assistant: {text}"))
        return text

    agent = Agent(
        "researcher",
        "Answers questions from a document pool",
        [fetch_document, think, select_context, answer],
        context_queue=memory,
        context_pool=ContextPool(limit=50),
    )

    async def ask(question: str, doc_ids: list[str]) -> None:
        await memory.append(ContextItem(content=f"User: {question}"))
        for doc_id in doc_ids:
            await agent.put(Turn(fetch_document, kwargs={"doc_id": doc_id}))
        await agent.put(Turn(think))
        async for turn, value in agent.run():
            if isinstance(value, str):
                print(f"[answer] {value}")

    asyncio.run(ask(
        question="What caused the November outage and has it been fixed?",
        doc_ids=list(DOCUMENTS.keys()),
    ))
    ```

## Install

```bash
uv add pygents py-ai-toolkit
```

## Setup

Configure the LLM and create the memory queue:

```python
import logging

from py_ai_toolkit import PyAIToolkit
from py_ai_toolkit.core.domain.interfaces import LLMConfig

from pygents import ContextQueue, ContextQueueHook, hook
from pygents.context import ContextItem

logger = logging.getLogger(__name__)
toolkit = PyAIToolkit(main_model_config=LLMConfig())

@hook(ContextQueueHook.AFTER_APPEND)
async def log_memory(items: list) -> None:
    logger.debug("ContextQueue: %d items", len(items))

memory = ContextQueue(limit=30, hooks=[log_memory])
```

!!! info "LLM configuration"
    `LLMConfig()` with no arguments reads from the environment. Set `LLM_MODEL`, `LLM_API_KEY`, and optionally `LLM_BASE_URL` (see [py-ai-toolkit Getting Started](https://paulomtts.github.io/py-ai-toolkit/getting-started/)).

## Document store

In production this would call an API, database, or vector store. For this example, documents live in a plain dict.

```python
DOCUMENTS = {
    "q3-earnings": {
        "description": "Q3 earnings report — revenue $42M (+18% YoY), gross margin 68%, guidance raised",
        "body": "Revenue: $42M. Gross margin: 68%. Net income: $8.2M. "
                "Guidance for Q4: $46–48M. Key driver: enterprise tier growth (+34%).",
    },
    "product-roadmap": {
        "description": "2025 product roadmap — three new integrations, mobile app beta, AI features",
        "body": "Q1: Slack and Jira integrations. Q2: Mobile app public beta. "
                "Q3: AI-assisted workflows. Q4: Enterprise audit logs.",
    },
    "incident-2024-11": {
        "description": "Incident report Nov 2024 — 47-minute outage, root cause: DB connection pool exhaustion",
        "body": "Duration: 47 min. Impact: 12% of API requests failed. "
                "Root cause: connection pool limit hit during traffic spike. "
                "Fix: pool size doubled; circuit breaker added.",
    },
    "hiring-plan": {
        "description": "2025 hiring plan — 8 engineers, 3 sales, 2 support; budget $3.2M",
        "body": "Engineering: 4 backend, 2 frontend, 1 ML, 1 DevOps. "
                "Sales: 2 AEs, 1 SDR. Support: 2 CSMs. Total budget: $3.2M.",
    },
    "security-audit": {
        "description": "Security audit results — SOC 2 Type II passed, 3 low-severity findings",
        "body": "Result: SOC 2 Type II certified. Findings: (1) session tokens not rotated on role change — fixed; "
                "(2) verbose error messages in staging — fixed; (3) MFA not enforced for API keys — in progress.",
    },
}
```

## Response models

```python
from pydantic import BaseModel

class ContextSelection(BaseModel):
    """IDs of the pooled items directly relevant to the question."""
    relevant_ids: list[str]

class Answer(BaseModel):
    """Final answer to the user's question."""
    answer: str
```

## The fetch_document tool

Returns a `ContextItem`. The tool has no knowledge of — or interaction with — the pool. The agent stores the item automatically when the turn completes.

```python
from pygents import tool
from pygents.context import ContextItem

@tool()
async def fetch_document(doc_id: str) -> ContextItem:
    """Fetch a document. The agent stores it in the context pool."""
    doc = DOCUMENTS.get(doc_id)
    if doc is None:
        raise ValueError(f"Unknown document: {doc_id!r}")
    return ContextItem(
        id=doc_id,
        description=doc["description"],
        content=doc["body"],
    )
```

!!! tip "Streaming ingestion with async generators"
    If you need to fetch many documents in one turn, use an async generator. Each yielded `ContextItem` is stored immediately as it's produced — the pool is populated incrementally rather than all at once:

    ```python
    @tool()
    async def fetch_documents(doc_ids: list[str]):
        for doc_id in doc_ids:
            doc = DOCUMENTS.get(doc_id)
            if doc:
                yield ContextItem(id=doc_id, description=doc["description"], content=doc["body"])
    ```

## The think tool

`think` receives the pool and memory via context injection — the agent provides its own instances automatically. It checks whether the pool has anything and routes accordingly. No LLM call, no writes.

```python
from pygents import ContextPool, ContextQueue, ToolHook, Turn

@hook(ToolHook.BEFORE_INVOKE)
async def log_pool_state(**kwargs) -> None:
    pool = kwargs.get("pool")
    if pool:
        logger.debug("think: pool has %d items\n%s", len(pool), pool.catalogue())

@tool(hooks=[log_pool_state])
async def think(pool: ContextPool, memory: ContextQueue) -> Turn:
    """Check if the pool has context; route to select_context or answer directly."""
    if not pool:
        return Turn(answer, kwargs={"relevant_ids": []})
    return Turn(select_context)
```

The logging concern lives in the hook, not in the tool body — `think` only routes. When a tool returns a `Turn`, the agent enqueues it and runs it next — that is how the chain `think → select_context → answer` forms without any external orchestration.

## The select_context tool

Sends only `id + description` lines to the LLM — never content. The LLM returns the relevant ids; those are forwarded to `answer` as the only explicitly passed kwarg.

```python
@tool()
async def select_context(pool: ContextPool, memory: ContextQueue) -> Turn:
    """Send descriptions to the LLM, collect relevant ids, hand off to answer."""
    question = latest_user_message(memory)
    response = await toolkit.asend(
        response_model=ContextSelection,
        template=(
            "Question: {{ question }}\n\n"
            "Available context items (id + one-line description only):\n{{ catalogue }}\n\n"
            "Return only the IDs of items directly relevant to answering this question."
        ),
        question=question,
        catalogue=pool.catalogue(),
    )
    return Turn(answer, kwargs={"relevant_ids": response.content.relevant_ids})
```

## The answer tool

`pool` and `memory` arrive via injection; `relevant_ids` is passed explicitly because it is computed by `select_context`, not provided by the agent. The reply is appended to memory and returned as a plain string — the agent yields it to the caller.

```python
@tool()
async def answer(pool: ContextPool, memory: ContextQueue, relevant_ids: list[str]) -> str:
    """Read selected item content from the pool and generate the final answer."""
    question = latest_user_message(memory)
    pool_ids = {item.id for item in pool.items}
    selected = [pool.get(id) for id in relevant_ids if id in pool_ids]

    if selected:
        context_block = "\n\n".join(
            f"[{item.id}] {item.description}\n{item.content}"
            for item in selected
        )
    else:
        context_block = "(no relevant context found)"

    response = await toolkit.asend(
        response_model=Answer,
        template=(
            "Answer this question using only the context below. "
            "Be concise and cite the source id.\n\n"
            "Question: {{ question }}\n\n"
            "Context:\n{{ context_block }}"
        ),
        question=question,
        context_block=context_block,
    )
    text = response.content.answer
    await memory.append(ContextItem(content=f"Assistant: {text}"))
    return text
```

## Helper

```python
def latest_user_message(memory: ContextQueue) -> str:
    for item in reversed(memory.items):
        if isinstance(item.content, str) and item.content.startswith("User:"):
            return item.content.removeprefix("User:").strip()
    return ""
```

## Putting it together

Pass `memory` and a fresh `ContextPool` to the agent — this is what makes context injection work. When `think`, `select_context`, and `answer` declare `pool: ContextPool` or `memory: ContextQueue`, the agent provides these exact instances automatically.

```python
import asyncio
from pygents import Agent, ContextPool, Turn

agent = Agent(
    "researcher",
    "Answers questions from a document pool",
    [fetch_document, think, select_context, answer],
    context_queue=memory,
    context_pool=ContextPool(limit=50),
)
```

### Append the user message first

Before enqueueing a `think` turn, append the user message to memory. Every tool reads context from there — no tool takes a raw message argument directly.

!!! warning "Append before enqueueing"
    If you call `agent.put(Turn(think))` without appending a user message first, every tool in the chain will see stale or empty context.

```python
async def ask(question: str, doc_ids: list[str]) -> None:
    await memory.append(ContextItem(content=f"User: {question}"))

    # Pre-load documents — the agent stores each ContextItem automatically
    for doc_id in doc_ids:
        await agent.put(Turn(fetch_document, kwargs={"doc_id": doc_id}))

    # pool and memory are injected; no kwargs needed on this turn
    await agent.put(Turn(think))

    async for turn, value in agent.run():
        if isinstance(value, str):
            print(f"[answer] {value}")

asyncio.run(ask(
    question="What caused the November outage and has it been fixed?",
    doc_ids=list(DOCUMENTS.keys()),
))
```

## Expected execution

1. **fetch_document × 5** — each returns a `ContextItem`; agent stores all five in the pool
2. **think** — pool has 5 items; logs the catalogue; routes to `select_context`
3. **select_context** — sends 5 descriptions (5 short lines) to the LLM; receives `["incident-2024-11"]`; routes to `answer`
4. **answer** — reads only `incident-2024-11` content from the pool; appends reply to memory; returns string

For `"What is the Q3 revenue and are there any open security findings?"`, step 3 returns `["q3-earnings", "security-audit"]` — two documents, not five. The other three bodies never leave the pool.

## Why descriptions matter

| Approach | Tokens per prompt | Scales to 200 items? |
|----------|------------------|----------------------|
| Dump entire pool into every prompt | `N × avg_content_size` | No — hits limits, high cost |
| Similarity search (vector DB) | Fixed retrieval window | Yes, but requires embedding infra |
| **LLM-driven description query** | **`N × avg_description_size` for selection, then only selected content** | **Yes — descriptions are tiny** |

Descriptions are typically 1–2 sentences. A pool of 200 items with 20-word descriptions adds roughly 700 tokens to the selection prompt — well within any model's budget. The answer prompt receives only the 2–5 selected items' full content.

!!! info "Extending ContextPool for external resources"
    `ContextPool` is designed to be subclassed. If your items live in an external store — a vector database, Redis, a relational table — you can override `add`, `remove`, `clear`, and `get` to proxy through that store while keeping the same interface that agents and tools expect. Pass your subclass instance to `Agent` via `context_pool`. `branch()` returns the correct subclass type automatically as long as your `__init__` accepts the same `limit` and `hooks` keyword arguments; otherwise override `branch()` as well.

## Summary

| Piece | Interaction | Role |
|-------|-------------|------|
| `fetch_document` | Returns `ContextItem` → agent auto-stores in pool | Produces items |
| `think` | Reads pool via injection — checks if populated, routes | Guards / routes |
| `select_context` | Reads pool descriptions via injection — LLM narrows to relevant ids | Selects |
| `answer` | Reads pool content via injection — only the selected ids | Injects and generates |
| `memory` | Appended to before first turn; tools read via injection; `answer` appends reply | Conversation thread |
| **Agent** | Stores `ContextItem` outputs; injects `memory` and `pool` into tools | Owns context |

For the API reference, see [Tools](../concepts/tools.md), [Context](../concepts/context.md), and [Hooks](../concepts/hooks.md).
