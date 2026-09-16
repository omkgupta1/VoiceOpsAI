# 0005 — A flow state's `purpose` is documentation, not a prompt

**Status:** Accepted (Phase 5)

## Context

Flow states carry a one-line `purpose` ("The booking is known. Act on it,
confirming before anything irreversible."). Appending it to the system prompt
looked free: one sentence of context about where the conversation is.

Measured on the 16-case tool-selection suite, 2 runs each:

| | no state line | + state line |
|---|---|---|
| all 7 tools | 75% | **47%** |
| flow-scoped | **81%** | 69% |

The line cost 28 points unscoped and 12 scoped. Worse, it nearly buried the
result this phase existed to establish: measured together, flow scoping appeared
to *hurt* (78% → 75%), when isolated it helps (75% → 81%).

## Decision

`purpose` is documentation for whoever reads the YAML. It is never sent to the
model. The tools a state offers are the instruction.

## Consequences

- The system prompt stays at 591 characters regardless of how many states a flow
  grows, so flows can be elaborated without degrading tool selection.
- Flow files stay self-documenting for humans, which was the point of `purpose`.
- Generalises the Phase 3 finding: on a small model, context is not free. Every
  sentence competes with the tool schemas for attention, and prose loses to a
  shorter list of tools.
- **Change one variable at a time.** Scoping and the state line were introduced
  together and the combined measurement pointed the wrong way. `make eval
  MODE=compare` exists so the comparison is one command.
