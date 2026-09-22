---
{
  "metadata": {"id": "review-integration", "phase_index": 4, "name": "Review and integration", "summary": "Integrate accepted artifacts and prepare final acceptance."},
  "participating_roles": ["Coordinator", "Integrator", "Reviewer"],
  "entry_criteria": {"required": ["implementation_manifest.md", "Reviewer sign-off"]},
  "exit_criteria": {"deliverables": ["release_packet.md"], "gate": "Final Acceptance"},
  "handoff_artifacts": [
    {"id": "release-packet", "from_phase": "review-integration", "to_phase": "intake", "producer_role": "Integrator", "consumer_role": "Owner", "artifact_schema": "release-packet"}
  ],
  "budget_slice": {"iterations": 3, "agent_turns": 6, "model_capability_units": 17, "wall_clock_minutes": 20},
  "escalation_gates": ["Final Acceptance", "regression failure", "hard limit"]
}
---

# Review and integration

Integrator composes accepted artifacts and regression evidence into `release_packet.md`. Reviewer audits acceptance criteria. Owner accepts, returns changes, or rejects at Final Acceptance.
