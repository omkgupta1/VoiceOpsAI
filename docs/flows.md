# Configurable flows

A flow is a **soft state machine** over a conversation. Each state declares which
tools are reachable from it. The model still drives the dialogue in its own
words — the customer is never forced down a script — but it can only *act* within
the current state.

Definitions live in [`services/ai/flows/`](../services/ai/flows/) as YAML, so which
operations are reachable, and from where, is a file you can read rather than
control flow buried in an orchestrator.

## The servicing flow

```
                   ┌──────────────┐
   call starts ──► │   identify   │  3 tools: flight status, booking lookup, escalate
                   └──────┬───────┘
                          │ get_booking succeeds
                          ▼
                   ┌──────────────┐
                   │  servicing   │  6 tools: cancel, reschedule options, refunds…
                   └──┬────────┬──┘
     get_reschedule_  │        │ cancel_booking
        options       ▼        ▼
            ┌─────────────┐  ┌──────────┐
            │choosing_    │  │ resolved │ (terminal)
            │  flight     ├─►└──────────┘
            └─────────────┘   reschedule_booking
```

`escalate_to_human` leads to a terminal `escalated` state from anywhere it is offered.

A **failed** lookup deliberately stays in `identify`: the servicing tools must not
become reachable for a booking the system could not find.

## Why it exists: measured, not assumed

Phase 3 found that a 7B model degrades when offered seven tools at once. Phase 5
tested whether narrowing per state fixes it. On the same 16-case suite, 3 runs each:

| | tool-selection accuracy |
|---|---|
| All tools at once | 73% |
| **Flow-scoped** | **81%** |

**+8% from scoping alone.**

### The finding that nearly hid it

The first implementation also appended each state's one-line `purpose` to the
system prompt — seemingly free context. Isolating the two variables:

| | no state line | + state line |
|---|---|---|
| all 7 tools | 75% | **47%** |
| flow-scoped | **81%** | 69% |

That single well-meant sentence cost **28%** unscoped and **12%** scoped. It was
doing more damage than the scoping repaired, and a combined test would have
concluded that scoping does not work.

So `purpose` stays in the YAML as documentation for humans and is **never sent to
the model**. The narrowed tool list is the instruction, and it is a far cheaper
one than prose.

> Change a flow, then run `make eval MODE=compare RUNS=3`. Do not reason about
> what a prompt "should" do to a small model.

## Writing a flow

```yaml
id: servicing
initial: identify

states:
  identify:
    purpose: Documentation only — never sent to the model.
    tools: [check_flight_status, get_booking, escalate_to_human]
    transitions:
      - tool: get_booking
        to: servicing            # defaults to on: success
      - tool: get_booking
        on: failure
        to: identify

  escalated:
    terminal: true
    tools: []
```

Validated at load, and again at startup:

- a transition to an undefined state is a startup error
- a transition on a tool the state does not offer is a startup error
- a tool name not in the registry is a startup error

All three would otherwise appear as a conversation that silently dead-ends, or an
agent that refuses to do something the flow says it can — near-impossible to
diagnose from a transcript.

## State is per call

The current flow and state are stored on the `calls` row (`flow_id`, and
`metadata.flow_state`), so a conversation resumes correctly across restarts and
the dashboard can show where any call sits.

## Known limitation

`escalate_to_human` is still missed roughly a third of the time for indirect
phrasings — "I'd like to speak to a manager" gets "Sure, I'll transfer you right
away" with no tool call, so the escalation is never recorded. This is a
model-capability ceiling rather than a flow bug: it persists at every tool count
tested. The provider interface exists for exactly this — `LLM_PROVIDER=groq`
puts a 70B model behind the same code.
