---
{
  "metadata": {"id": "research-planning", "phase_index": 2, "name": "Research and planning", "summary": "Turn evidence into a buildable plan."},
  "participating_roles": ["Coordinator", "Researcher", "Author"],
  "entry_criteria": {"required": ["task_brief.md", "Owner Start approval"]},
  "exit_criteria": {"deliverables": ["task_spec.md"], "gate": "Plan-to-Build"},
  "handoff_artifacts": [
    {"id": "task-spec", "from_phase": "research-planning", "to_phase": "implementation", "producer_role": "Author", "consumer_role": "Builder", "artifact_schema": "task-spec"}
  ],
  "budget_slice": {"iterations": 3, "agent_turns": 8, "model_capability_units": 30, "wall_clock_minutes": 35},
  "escalation_gates": ["Plan-to-Build", "scope expansion", "architectural shift"]
}
---

# Research and planning

Researcher records evidence in `findings.md`; Author produces `task_spec.md`; Coordinator aligns `task_plan.md`. Owner approves Plan-to-Build before implementation.
