# Orca orchestration adapter runbook

Use only when Owner explicitly selects `mode: orca`. Neutral orchestration
templates remain the source of roles, budgets, gates, and handoffs.

## Mapping

- One Owner task → one Orca Run.
- Intake, Research/Planning, Implementation, Review/Integration → four Orca
  Tasks, linked in dependency order.
- A bounded role assignment → Dispatch.
- A human gate → coordinator-owned decision Task. Block dependents until Owner
  structured reply is recorded.

## Safety

- Begin with dry-run. It makes no Orca calls.
- `--apply` creates only disposable Run/Task/DAG state.
- `--execute` is separately explicit and requires recorded Start approval
  before Dispatch or worktree creation.
- One Implementation Task owns one worktree. Overlapping Builder writes are
  serial. Parallel writes need child worktrees after Coordinator path review.
- Reviewer is read-only. Never delete a branch automatically.

## Audit and recovery

Write `.planning/<plan-id>/orca/run-map.json` and decision packets. Treat Orca
Run/Task state as operational control plane. On an interrupted run, read the
audit trail and Orca task state before any retry; do not recreate a Run or
Dispatch blindly.
