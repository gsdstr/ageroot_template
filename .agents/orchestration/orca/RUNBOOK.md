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

## Coordinator event loop

Orca inbox is decoupled from Antigravity's message bus. The coordinator must
maintain active supervision after dispatching a worker:

```bash
# Wait for batch
orca orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json

# Process all messages in delivery:
# 1. On question (ask): reply immediately
orca orchestration reply --id <message_id> --body "<answer>" --json

# 2. On worker_done: validate output against active dispatch, decide reuse / release / retain
orca orchestration worker-release --dispatch <dispatch_id> --json

# 3. Acknowledge processed batch and resume wait
orca orchestration check --ack <delivery_id> --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json
```

- When receiving `question` (`ask`): reply promptly. Do not let workers stall into timeout.
- When human decision is required: pause branch and present structured Decision Packet to Owner; keep supervision live.
- Acknowledge a Delivery batch (`--ack <delivery_id>`) only AFTER processing its messages.

## Role-to-harness dispatch mapping

Harness and model selection are governed by role assignment in `protocols/orchestration.md`:
- **Builder**: defaults to `agy` (e.g. `gemini-3.8-flash-high`), fallback `codex`.
- **Reviewer**: defaults to `codex` (e.g. `codex-auto-review` or `gpt-5.6-terra`), fallback `agy`. Reviewer must maintain model-family independence from Builder.
- **Author**: defaults to `codex` (`gpt-5.6-sol`).
- **Researcher**: defaults to `agy` (`gemini-3.1-pro-high`).

Never pass a single hardcoded harness default across all tasks.
Resolve executables portably (`agy`, `codex`, `claude`) from PATH or environment, never machine-local absolute paths.

When launching Orca workers:
- If `--agent <harness>` is supported by Orca runtime for that harness, pass `--agent <harness>`.
- If harness runs as a CLI command (e.g. `agy`), create the terminal (`orca terminal create --worktree <wt> --command "<harness>" --json`) and bind via `worker-start --task <id> --terminal <handle> --json`.

## Audit and recovery

Write `.planning/<plan-id>/orca/run-map.json` and decision packets. Treat Orca
Run/Task state as operational control plane. On an interrupted run, read the
audit trail and Orca task state before any retry; do not recreate a Run or
Dispatch blindly.

