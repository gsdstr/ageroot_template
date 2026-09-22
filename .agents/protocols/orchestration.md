# Orchestration Protocol

Portable agent orchestration for end-to-end task lifecycles across diverse agent harnesses (Antigravity, Codex, Hermes, Pi) and subscription-accessible model inventories.

## Core Principles

1. **Agent-neutral roles**: Roles define capabilities, authority, and boundaries, decoupled from specific agent harnesses or model vendors.
2. **Owner primacy**: The human Owner retains sole authority for task objectives, budget limits, mandatory gates, and final acceptance.
3. **Planning-with-files alignment**: Lifecycle artifacts directly populate persistent planning structures (`.planning/<plan-id>/`), maintaining context integrity across agent boundaries.
4. **Independent review**: Reviewers operate with full blocking authority on correctness and safety; deadlock at iteration limits escalates to the Owner via structured Decision Packets.

---

## Canonical Roles

| Role | Capability Profile | Default Agent & Model | Fallback Agent & Model | Authority & Boundaries |
|---|---|---|---|---|
| **Owner** | Human | Human User | None | Final authority; does not execute operational steps directly. |
| **Coordinator** | `fast` / `reasoning` | `agy`: `gemini-3.8-flash-high` | `codex`: `gpt-5.6-sol` | Directs lifecycle and plan; never assumes Owner authority. |
| **Researcher** | `reasoning` | `agy`: `gemini-3.1-pro-high` | `codex`: `gpt-6-astra` | Produces `findings.md`; does not implement operational code. |
| **Author** | `reasoning` / `fast` | `codex`: `gpt-5.6-sol` | `agy`: `gemini-3.1-pro-high` | Drafts specs, briefs, docs; does not write code. |
| **Builder** | `coding` | `agy`: `gemini-3.8-flash-high` | `codex`: `gpt-5.6-terra` | Implements code/config; must pass independent review. |
| **Reviewer** | `review` | `codex`: `codex-auto-review` or `gpt-5.6-terra` | `agy`: `gemini-3.1-pro-high` | Independent evaluation; holds blocking authority; must be orthogonal to Builder. |
| **Integrator** | `coding` / `fast` | `agy`: `gemini-3.8-flash-high` | `codex`: `gpt-5.6-terra` | Composes changes and verifies regressions; does not alter logic. |

---

## Budget, Iterations, and Human Gates

### Standard Budget Profile
- **Run-level iterations**: 12
- **Iterations per role/output**: 3
- **Builder–Reviewer rework cycles**: 3
- **Agent turns**: 30
- **Model-capability units**: 100
- **Wall-clock time**: 120 minutes

### Gate Triggers
1. **Budget Warning (80%)**: Coordinator alerts Owner with plan-versus-actual metrics and completion forecast; execution continues.
2. **Budget Hard Gate (100%)**: Execution halts immediately; Coordinator sends Decision Packet. No new tasks start until Owner re-baselines, reduces scope, or stops.
3. **Lifecycle Gates**: Mandatory pauses requiring Owner sign-off at **Start**, **Plan-to-Build**, and **Final Acceptance**.
4. **Risk Gates**: Immediate pause on agent deadlocks, missing credentials/access, or irreversible operations.

### Decision Packet Schema
Every human gate receives a structured Decision Packet:
- **Context**: Summary of current state and triggering condition.
- **Options**: Viable courses of action with trade-offs.
- **Recommendation**: Coordinator-recommended option.
- **Impact Analysis**: Projected impact on scope, budget, and risk.

---

## Lifecycle Composition & Phase Templates

The development source is `templates/orchestration/v1/`. A portable template
distribution lives at `.agents/orchestration/` and must contain the same
`manifest.yaml`, four `phases/*.md`, `lifecycle-map.md`, and `schemas/`
contracts. A runtime adapter is not implied.

```
01-intake  ──[Start Gate]──>  02-research-planning  ──[Plan-to-Build Gate]──>  03-implementation  ──>  04-review-integration  ──[Final Acceptance Gate]──>  Done
     ▲                                   ▲                                             │                            │
     │                                   └────────────── [Change Request] ─────────────┘                            │
     └────────────────────────────────────────────────── [Regression Loopback] ─────────────────────────────────────┘
```

### 1. Phase 01: Intake
- **Roles**: Owner, Coordinator.
- **Goal**: Clarify requirements, define scope boundaries, select budget profile.
- **Handoff Artifact**: `task_brief.md` → seeds `.planning/<plan-id>/task_plan.md`.
- **Exit Gate**: Owner Start Gate.

### 2. Phase 02: Research & Planning
- **Roles**: Coordinator, Researcher, Author.
- **Goal**: Investigate technical feasibility, establish architecture, define milestones and test plan.
- **Handoff Artifact**: `task_spec.md` + updated `task_plan.md` + `findings.md`.
- **Exit Gate**: Owner Plan-to-Build Gate.

### 3. Phase 03: Implementation
- **Roles**: Coordinator, Builder, Reviewer.
- **Goal**: Execute code/config changes, run unit tests, perform independent code review up to 3 rework cycles.
- **Handoff Artifact**: `implementation_manifest.md` + worker reports (`workers/<task-id>.md`) + diffs.
- **Exit Condition**: Reviewer sign-off on all acceptance criteria.

### 4. Phase 04: Review & Integration
- **Roles**: Coordinator, Integrator, Reviewer.
- **Goal**: Merge changes, execute full regression test suite, verify end-to-end functionality.
- **Handoff Artifact**: `release_packet.md` + audit trail in `progress.md`.
- **Exit Gate**: Owner Final Acceptance Gate.

---

## Inter-phase Loopback & Arbitration Rules

1. **Two-Tier Loopback**:
   - *Autonomous Loopback*: If implementation encounters an unpredicted spec deficit that is non-breaking and within budget, Builder emits a `change_request.md`. Coordinator routes back to Phase 02 for targeted plan adjustment.
   - *Escalation Loopback*: Breaking architectural changes, scope expansions, or limit overruns halt the phase and trigger an Owner Decision Packet.
2. **Conflict Arbitration**:
   - A Reviewer objection blocks progress. Builder iterates up to 3 cycles.
   - If agreement cannot be reached after 3 cycles, Coordinator halts the branch, compiles the Reviewer objection and Builder rationale, and escalates to the Owner. Coordinator never overrides a Reviewer.

## Fallback Policy

- **Intra-subscription**: a role may use its configured fallback on the same
  subscription automatically when quota or availability requires it.
- **Cross-subscription**: Coordinator pauses the affected branch and sends an
  Owner Decision Packet before moving between Antigravity and Codex.
- **Tier 3**: Hermes and Pi are metered emergency surfaces. Coordinator must
  obtain Owner approval before invoking either one.

## Static Contract

The package validates without a runtime. Each phase must declare the seven
frontmatter fields (`metadata`, `participating_roles`, `entry_criteria`,
`exit_criteria`, `handoff_artifacts`, `budget_slice`, `escalation_gates`).
Handoffs must name producer, consumer, phase edge, and artifact schema; their
budget slices must remain within the standard profile.
