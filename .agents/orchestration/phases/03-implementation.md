---
{
  "metadata": {"id": "implementation", "phase_index": 3, "name": "Implementation", "summary": "Build and independently review executable artifacts."},
  "participating_roles": ["Coordinator", "Builder", "Reviewer"],
  "entry_criteria": {"required": ["task_spec.md", "Owner Plan-to-Build approval"]},
  "exit_criteria": {"deliverables": ["implementation_manifest.md"], "verification": "Reviewer sign-off", "reviewer_independence": true},
  "handoff_artifacts": [
    {"id": "implementation-manifest", "from_phase": "implementation", "to_phase": "review-integration", "producer_role": "Builder", "consumer_role": "Integrator", "artifact_schema": "implementation-manifest"},
    {"id": "change-request", "from_phase": "implementation", "to_phase": "research-planning", "producer_role": "Coordinator", "consumer_role": "Author", "artifact_schema": "change-request"}
  ],
  "budget_slice": {"iterations": 4, "agent_turns": 12, "model_capability_units": 45, "wall_clock_minutes": 55},
  "escalation_gates": ["Reviewer blocking objection", "three rework cycles", "hard limit"]
}
---

# Implementation

Builder produces executable artifacts and `implementation_manifest.md`. Reviewer is independent from Builder's harness and model family, blocks correctness or safety failures, and permits at most three rework cycles. Coordinator escalates unresolved objections to Owner and never overrides Reviewer.
