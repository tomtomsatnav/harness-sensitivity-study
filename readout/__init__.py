"""Readout study: a like-for-like replication of the "readout" interface in arXiv 2610.37255
("Your Policy Is Secretly a Harness", Table 1 / Appendix F.2) for Claude models.

Each decision, the actor gets a system message (task + harness rules) and a user message
holding a JSON observation. Every complete action maps one-to-one to a single-character code
(A-Z then a-z); the model replies with only the code. Three MiniWoB++-style task families are
reimplemented as small deterministic simulations (no browser): click-checkboxes,
click-checkboxes-soft and ascending-numbers. Conditions R0-R5 change only how the observation
and the code menu are presented, never the semantic actions, the execution or the optimal policy.

Design choices (see also README.md):
- The full multi-turn message history is kept, like a normal chat: each observation is a new
  user turn and each reply an assistant turn. ascending-numbers relies on this, since numbers
  are hidden after the first click and must be recalled from the first observation.
- An unparseable or illegal reply is a failure step: it does nothing and consumes a decision.
"""

from readout.conditions import CONDITIONS, Condition
from readout.tasks import ALL_INSTANCES, FAMILIES, INSTANCES_BY_ID, MAX_DECISIONS, Instance

__all__ = ["ALL_INSTANCES", "CONDITIONS", "Condition", "FAMILIES", "INSTANCES_BY_ID", "Instance", "MAX_DECISIONS"]
