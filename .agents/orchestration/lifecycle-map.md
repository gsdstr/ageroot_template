---
{
  "phase_order": ["intake", "research-planning", "implementation", "review-integration"],
  "owner_gates": [
    {"name": "Start", "decision": "approve, revise, or stop objective/scope/budget"},
    {"name": "Plan-to-Build", "decision": "proceed, revise plan, reduce scope, or stop"},
    {"name": "Final Acceptance", "decision": "accept, return required changes, or reject"}
  ],
  "loopbacks": [
    {"kind": "autonomous", "from": "implementation", "to": "research-planning", "artifact": "change-request", "condition": "minor deficit within remaining budget"},
    {"kind": "escalation", "from": "implementation", "to": "Owner", "condition": "architecture, scope, review-limit, or hard-limit change"}
  ]
}
---

# Neutral orchestration lifecycle map

`intake → research/planning → implementation → review/integration`. The affected branch pauses at an Owner gate; independent branches may continue within their own limits.
