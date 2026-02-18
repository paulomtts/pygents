# Structural Principles for Agent Systems

---

## Abstract

Orchestrating large language model-based agents requires making a set of foundational decisions about how concerns are separated, how state is managed, and where responsibility resides. This document proposes one coherent answer to those questions: a structural approach grounded in the separation of *declaration*, *implementation*, and *orchestration* as three irreducible concerns, realized through a minimal set of primitives with explicit responsibilities. The argument is that structural discipline — enforced by the design of the primitives themselves, not by convention — is the primary source of composability and evolvability in agent systems. What follows is a philosophical case for the decisions that produce this property, intended for engineers designing or evaluating agent orchestration systems from first principles.

---

## 1. Core Thesis

A well-designed agent system provides structure — primitives with clear invariants and enforced separation of concerns — and nothing more. The framework's scope is coordination; the application's scope is everything else.

Three concerns are irreducible in any orchestrated agent system, and each must remain ignorant of the others to do its job:

**Implementation** is the realization of a unit of work: a function of its inputs, without awareness of queues, context stores, or the session surrounding it. It does not know when it will be called, by whom, or what state has accumulated before it runs. This ignorance is what makes implementations composable: a unit of work that holds no reference to the system invoking it can be used by any orchestrator, tested without a runtime, and replaced without disturbing anything that coordinates it.

**Declaration** is the expression of intent: which action to invoke, with which arguments, under which constraints. It does not know how the work is carried out, when it will run, or what the orchestrator will do with its output. A declaration that knows nothing beyond its own intent can be described entirely as data — serialized, transmitted, and replayed without reconstructing any runtime state.

**Orchestration** is the coordination of declarations: resolving what is named, dispatching execution, routing results, and maintaining accumulated state. It does not know how any individual implementation works and does not make decisions based on the semantic content of results — only on their type. This is what makes the orchestrator substitutable: a different coordinator can process the same queue of declarations without changing anything about the implementations or the intent they carry.

The three concerns are individually justifiable, but their value is relational. Each is worth exactly what the others allow it to be. An implementation that reaches into the context store is no longer a pure function of its inputs; a declaration that embeds execution logic is no longer data; an orchestrator that interprets results is no longer a pure coordinator. The integrity of the system is not what happens when each concern does less — it is what happens when each is designed so that doing more is structurally awkward.

---

## 2. Primitive Decomposition

Structural separation is only possible if the primitives that enforce it are well-defined. The following five primitives constitute the minimal necessary vocabulary for a conforming agent system. Each has exactly one responsibility.

### 2.1 The Recipe

An recipe is a pure, stateless unit of implementation. It receives arguments and produces a result. It has no knowledge of the context it will be invoked in, the queue it was drawn from, or the state stores that surround it. It has one job

### 2.2 The Work Unit

A work unit is a declaration of intent. It references a specific recipe and binds it to a set of arguments and constraints — specifying what should be invoked, with what inputs, and under what conditions. It has no behavior of its own. It is the object that travels through the system, can be serialized, and can be replayed.

The output of one work unit may itself be another work unit. This is the sanctioned mechanism for dynamic task creation: an implementation that determines further work is needed returns a declaration, and the orchestrator handles the enqueueing. The implementation remains unaware of the queue.

### 2.3 The Orchestrator

The orchestrator owns a queue of work units and processes them in order. It resolves each work unit's named action, dispatches execution, and routes the output. Its job is coordination; it does not interpret outputs or make domain decisions.

The orchestrator is the sole writer to the context store. When an action produces a result intended for storage, it signals that intent in its output. The orchestrator recognizes the intent and performs the write. This arrangement means the store's contents are entirely determined by the orchestrator's processing history: every item has a corresponding work unit that produced it, and the provenance of any item is visible without inspecting any action's implementation.

### 2.4 Working Context

Working context is a bounded, ordered sequence of recent items — the immediate history that makes an agent's current task coherent. The capacity limit is a property of the primitive, not a policy applied at the application layer. A session operates within a defined window, and the window is maintained automatically as new items are appended. Working context is sequential — appended to and read in order — which is a different access pattern from accumulated context. Keeping them separate preserves the efficiency of both.

### 2.5 Accumulated Context

Accumulated context is the structured record of what a session has produced — results, observations, and items the agent has gathered and may need to reference. It is keyed: items are retrieved by identifier, not by position.

Each item carries two things: a description and a content payload. The description is a summary intended for selection — the process of deciding which items are relevant to the current task. Selection operates on descriptions; retrieval operates on content. Keeping these separate means selection can be performed without loading the full content of every candidate, and the cost of selection scales with the number of items rather than with the size of their content.

The framework provides the structural separation. What the application puts in the description, and what mechanism it uses to select among them, remains entirely within the application's domain.

---

## 3. Conclusion

Structural discipline is what allows an agent system to remain coherent as it grows. When declaration, implementation, and orchestration are kept separate — by the design of the primitives, not by convention — each concern can be reasoned about, tested, and replaced independently. The system's composability is a direct consequence of the separation, not an incidental property of good implementation.

The value of this approach lies in what the structure makes natural: actions that are reusable across contexts, work units that are reproducible from their data alone, a context store whose provenance is always visible, and state that is bounded by construction. These are not features added on top of the architecture. They are properties that emerge from the architecture — from defining primitives with exactly one responsibility each and letting the relationships between them enforce the rest.
