# Structural Principles for Agent Systems

---

## Abstract

Orchestrating large language model-based agents requires making a set of foundational decisions about how concerns are separated, how state is managed, and where responsibility resides. This document proposes one coherent answer to those questions: a structural approach grounded in the separation of *declaration*, *implementation*, and *orchestration* as three irreducible concerns, realized through a minimal set of primitives with explicit responsibilities. The argument is that structural discipline — enforced by the design of the primitives themselves, not by convention — is the primary source of composability and evolvability in agent systems.

---

## Core Thesis

A well-designed agent system provides structure — primitives with clear invariants and enforced separation of concerns — and nothing more. The framework's scope is coordination; the application's scope is everything else.

Three concerns are irreducible in any orchestrated agent system, and each must remain ignorant of the others to do its job:

**Implementation** contains the instructions for the realization a unit of work: a function of its inputs, without awareness of queues, context stores, or the session surrounding it. It does not know when it will be called, by whom, or what state has accumulated before it runs. This ignorance is what makes implementations composable: a unit of work that holds no reference to the system invoking it can be used by any orchestrator, tested without a runtime, and replaced without disturbing anything that coordinates it.

**Declaration** is the expression of intent: which action to invoke, with which arguments, under which constraints. It does not know how the work is carried out, when it will run, or what the orchestrator will do with its output. A declaration that knows nothing beyond its own intent can be described entirely as data — serialized, transmitted, and replayed without reconstructing any runtime state.

**Orchestration** is the coordination of declarations: resolving what is named, dispatching execution, routing results, and maintaining accumulated state. It does not know how any individual implementation works and does not make decisions based on the semantic content of results — only on their type. This is what makes the orchestrator substitutable: a different coordinator can process the same queue of declarations without changing anything about the implementations or the intent they carry.

The three concerns are individually justifiable, but their value is relational. Each is worth exactly what the others allow it to be. An implementation that reaches into a context store is no longer a pure function of its inputs; a declaration that embeds execution logic is no longer data; an orchestrator that interprets results is no longer a pure coordinator. The integrity of the system is not what happens when each concern does less — it is what happens when each is designed so that doing more is structurally awkward.

---

## Primitive Decomposition

Structural separation is only possible if the primitives that enforce it are well-defined. The following five primitives constitute the minimal necessary vocabulary for a conforming agent system. Each has exactly one responsibility.

### The Tool

An Tool is a pure, stateless unit of work, and expresses instructions for realizing something. A Tool may well return a Turn, in which case the Agent will be obliged to enqueue it for later execution.

### The Turn

A Turn is a declaration of intent. It references a specific Tool and binds it to a set of arguments — specifying what should be invoked, with what inputs. It has no behavior of its own.

### The Agent

The Agent owns a queue of Turns and processes them in order. It dispatches execution, and routes the output. Its job is coordination; it does not interpret outputs or make domain decisions. 

In addition, it acts as the sole writer to the context stores - when Tools pproduce result intended as context, that intent is expressed in the type of its output. The Agent recognizes the intent and performs the write. 

---

## Conclusion

Structural discipline is what allows an agent system to remain coherent as it grows. When declaration, implementation, and orchestration are kept separate — by the design of the primitives, not by convention — each concern can be reasoned about, tested, and replaced independently. The system's composability is a direct consequence of the separation, not an incidental property of good implementation.

The value of this approach lies in what the structure makes natural: actions that are reusable across contexts, work units that are reproducible from their data alone, a context store whose provenance is always visible, and state that is bounded by construction.
