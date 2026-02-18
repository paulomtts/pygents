# LLM-Driven Context Querying

This guide builds a **research assistant** that accumulates documents in a `ContextPool` and answers questions by **querying the pool with the LLM** — presenting lightweight descriptions to decide which documents are relevant, then injecting only those contents into the answer prompt.

The pattern prevents token explosion: a pool of 50 documents adds only 50 short description lines to a selection prompt rather than 50 full document bodies. The LLM selects the relevant subset; only that subset reaches the answer prompt.

## The rule

> **Tools never write to the pool.** Writing is the agent's responsibility. A fetch tool simply returns a `ContextItem`; the agent stores it automatically. Tools that need context receive `pool` as a parameter and **read** from it — descriptions for selection, content for injection. That's it.

This means:

| Who | Can do |
|-----|--------|
| Fetch tool | Return `ContextItem` → agent auto-stores it |
| Think / select / answer tools | Read `pool.catalogue()` or `pool.get(id)` |
| **Nobody** | Call `pool.add()`, `pool.remove()`, or `pool.clear()` |

The user (or orchestration code) decides which documents to pre-load by queuing fetch turns before the reasoning chain runs.

## What you will build

Four tools:

1. **`fetch_document`** — fetches one document, returns a `ContextItem` (auto-pooled by agent, no pool interaction in the tool itself)
2. **`think`** — reads pool descriptions to check if context is sufficient, routes to `select_context`
3. **`select_context`** — reads pool descriptions, asks the LLM which are relevant, routes to `answer` carrying the selected ids
4. **`answer`** — reads selected item content from the pool, generates the final answer

The code blocks below are meant to be combined into one script.

## Install

```bash
uv add pygents py-ai-toolkit
```

```python
import logging

from py_ai_toolkit import PyAIToolkit
from py_ai_toolkit.core.domain.interfaces import LLMConfig

from pygents import ContextQueue, ContextQueueHook, hook

logger = logging.getLogger(__name__)

toolkit = PyAIToolkit(main_model_config=LLMConfig())

@hook(ContextQueueHook.AFTER_APPEND)
async def log_memory(items: list) -> None:
    logger.debug("ContextQueue: %d items", len(items))

memory = ContextQueue(limit=30, hooks=[log_memory])
```

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

## The fetch_document tool

Returns a `ContextItem`. The tool has no knowledge of — or interaction with — the pool. The agent stores the item automatically when the turn completes.

```python
from pygents import tool, Turn
from pygents.context_pool import ContextItem, ContextPool

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

## The think tool

`think` receives the pool as a read-only view of what's been fetched so far. If the pool is empty it routes directly to `answer` with no ids; otherwise it logs the catalogue and hands off to `select_context`. No LLM call, no writes.

```python
@tool()
async def think(pool: ContextPool, memory: ContextQueue) -> Turn:
    """Read pool descriptions, decide if enough context exists, route to select_context."""
    if not pool:
        # Nothing in the pool — answer directly from memory
        return Turn(answer, kwargs={
            "pool": pool, "relevant_ids": [], "memory": memory
        })

    catalogue = pool.catalogue()
    logger.debug("think: pool has %d items\n%s", len(pool), catalogue)

    return Turn(select_context, kwargs={"pool": pool, "memory": memory})
```

## The select_context tool

`select_context` sends only `id + description` to the LLM, never content. The LLM returns the ids it considers relevant; those ids are forwarded to `answer` as a plain kwarg.

```python
@tool()
async def select_context(pool: ContextPool, memory: ContextQueue) -> Turn:
    """Send descriptions to the LLM, collect relevant ids, hand off to answer."""
    question = latest_user_message(memory)

    catalogue = pool.catalogue()

    response = await toolkit.asend(
        response_model=ContextSelection,
        template=(
            "Question: {{ question }}\n\n"
            "Available context items (id + one-line description only):\n{{ catalogue }}\n\n"
            "Return only the IDs of items directly relevant to answering this question."
        ),
        question=question,
        catalogue=catalogue,
    )

    return Turn(
        answer,
        kwargs={
            "pool": pool,
            "relevant_ids": response.content.relevant_ids,
            "memory": memory,
        },
    )
```

## The answer tool

`answer` receives the selected ids and reads the full content of those items from the pool. Only the selected items' content reaches the LLM at this step.

```python
@tool()
async def answer(pool: ContextPool, relevant_ids: list[str], memory: ContextQueue) -> str:
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
    await memory.append(f"Assistant: {text}")
    return text
```

## Helper

```python
def latest_user_message(memory: ContextQueue) -> str:
    for item in reversed(memory.items):
        if isinstance(item, str) and item.startswith("User:"):
            return item.removeprefix("User:").strip()
    return ""
```

## Putting it together

The user queues fetch turns for the documents they want available, then queues the reasoning chain. The agent runs them in order — fetch turns auto-populate the pool, then `think` → `select_context` → `answer` uses it.

```python
import asyncio
from pygents import Agent, Turn
from pygents.context_pool import ContextPool

pool = ContextPool(limit=50)

agent = Agent(
    "researcher",
    "Answers questions from a document pool",
    [fetch_document, think, select_context, answer],
    context_pool=pool,
)

async def ask(question: str, doc_ids: list[str]) -> None:
    await memory.append(f"User: {question}")

    # Pre-load documents — the agent stores each ContextItem automatically
    for doc_id in doc_ids:
        await agent.put(Turn(fetch_document, kwargs={"doc_id": doc_id}))

    # Reasoning chain reads from the pool — no tool writes to it
    await agent.put(Turn(think, kwargs={"pool": pool, "memory": memory}))

    async for turn, value in agent.run():
        if isinstance(value, str):
            print(f"[answer] {value}")

asyncio.run(ask(
    question="What caused the November outage and has it been fixed?",
    doc_ids=list(DOCUMENTS.keys()),  # pre-load all; select_context narrows it down
))
```

Expected execution:

1. **fetch_document × 5** — each returns a `ContextItem`; agent stores all five in `pool`
2. **think** — pool has 5 items; logs the catalogue; routes to `select_context`
3. **select_context** — sends 5 descriptions (5 short lines) to LLM; receives `["incident-2024-11"]`; routes to `answer`
4. **answer** — reads only `incident-2024-11` content from pool; generates answer

For `"What is the Q3 revenue and are there any open security findings?"`, step 3 returns `["q3-earnings", "security-audit"]` — two documents, not five. The other three bodies never leave the pool.

## Why this matters

| Approach | Tokens per prompt | Scales to 200 items? |
|----------|------------------|----------------------|
| Dump entire pool into every prompt | `N × avg_content_size` | No — hits limits, high cost |
| Similarity search (vector DB) | Fixed retrieval window | Yes, but requires embedding infra |
| **LLM-driven description query** | **`N × avg_description_size` for selection, then only selected content** | **Yes — descriptions are tiny** |

Descriptions are typically 1–2 sentences. A pool of 200 items with 20-word descriptions adds roughly 700 tokens to the selection prompt — well within any model's budget. The answer prompt receives only the 2–5 selected items' full content.

!!! info "Extending ContextPool for external resources"
    `ContextPool` is designed to be subclassed. If your items live in an external store — a vector database, Redis, a relational table — you can override `add`, `remove`, `clear`, and `get` to proxy through that store while keeping the same interface that agents and tools expect. Pass your subclass instance to `Agent` via the `context_pool` parameter. `branch()` will return the correct subclass type automatically as long as your `__init__` accepts the same `limit` and `hooks` keyword arguments; otherwise override `branch()` as well.

## Summary

| Piece | Pool interaction | Role |
|-------|-----------------|------|
| `fetch_document` | None — returns `ContextItem`, agent stores it | Produces items |
| `think` | **Read only** — checks if pool is populated, routes | Guards / routes |
| `select_context` | **Read only** — reads `item.id` + `item.description` | Selects relevant ids |
| `answer` | **Read only** — reads `item.content` of selected ids | Injects and generates |
| **Agent** | **Write** — stores `ContextItem` outputs automatically | Owns the pool |

For the `ContextPool` API reference, see [Context Pool](../concepts/context_pool.md).
