#!/usr/bin/env python3
"""Dry-run mapper from neutral lifecycle to Orca supervised state."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess

PHASES = ["intake", "research-planning", "implementation", "review-integration"]

DEFAULT_PHASE_ROLES = {
    "intake": {"role": "Coordinator", "harness": "agy"},
    "research-planning": {"role": "Researcher", "harness": "agy"},
    "implementation": {"role": "Builder", "harness": "agy"},
    "review-integration": {"role": "Reviewer", "harness": "codex"},
}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--mode", choices=["neutral", "orca"], default="orca")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-approved", action="store_true")
    parser.add_argument("--role-harness-override", help="JSON string of role:harness overrides")
    args = parser.parse_args()
    if args.mode != "orca":
        parser.error("adapter requires --mode orca")
    if args.execute and not args.start_approved:
        parser.error("--execute requires recorded Owner Start approval")
    audit = f".planning/{args.plan_id}/orca"
    commands = [["orca", "orchestration", "run-create", "--objective", args.plan_id, "--json"]]
    wait_loop_command = [
        "orca",
        "orchestration",
        "check",
        "--wait",
        "--types",
        "worker_done,escalation,question",
        "--timeout-ms",
        "900000",
        "--json",
    ]
    harness_overrides = json.loads(args.role_harness_override) if args.role_harness_override else {}
    tasks = []
    for index, phase in enumerate(PHASES):
        role_info = DEFAULT_PHASE_ROLES[phase]
        role = role_info["role"]
        harness = harness_overrides.get(role, role_info["harness"])
        tasks.append({
            "phase": phase,
            "role": role,
            "assigned_harness": harness,
            "depends_on": PHASES[index - 1:index],
        })
    plan = {
        "mode": "execute-plan" if args.execute else "apply" if args.apply else "dry-run",
        "run": {"owner_task": args.plan_id},
        "tasks": tasks,
        "decision_tasks": ["Start", "Plan-to-Build", "Final Acceptance"],
        "audit": {"run_map": f"{audit}/run-map.json", "decision_packets": f"{audit}/decision-packets/"},
        "coordinator_wait_loop": wait_loop_command,
        "orca_calls": [] if not args.apply else commands,
        "planned_orca_commands": commands,
    }
    if args.apply:
        receipt = json.loads(subprocess.check_output(commands[0], text=True))
        plan["apply_receipt"] = receipt
        plan["warning"] = "Run created; task DAG/Dispatch remains explicit adapter follow-up."
    if args.execute:
        plan["warning"] = (
            "Start approved; worktree/Dispatch commands require reviewed task specs and role-to-harness mapping. "
            "Coordinator must maintain active supervision loop."
        )
    print(json.dumps(plan, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
