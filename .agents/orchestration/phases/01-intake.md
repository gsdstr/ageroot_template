---
{
  "metadata": {"id": "intake", "phase_index": 1, "name": "Intake", "summary": "Define objective, scope, and budget."},
  "participating_roles": ["Owner", "Coordinator"],
  "entry_criteria": {"required": ["Owner request"]},
  "exit_criteria": {"deliverables": ["task_brief.md"], "gate": "Start"},
  "handoff_artifacts": [
    {"id": "task-brief", "from_phase": "intake", "to_phase": "research-planning", "producer_role": "Coordinator", "consumer_role": "Researcher", "artifact_schema": "task-brief"}
  ],
  "budget_slice": {"iterations": 2, "agent_turns": 4, "model_capability_units": 8, "wall_clock_minutes": 10},
  "escalation_gates": ["Start", "missing access", "irreversible action"]
}
---

# Intake

Owner and Coordinator produce `task_brief.md`: objective, scope, acceptance criteria, budget profile, and required Owner gates. Owner approves Start before research begins.
