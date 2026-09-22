#!/usr/bin/env python3
"""Dry-run mapper from neutral lifecycle to Orca supervised state."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess

PHASES = ["intake", "research-planning", "implementation", "review-integration"]

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--mode", choices=["neutral", "orca"], default="orca")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-approved", action="store_true")
    args = parser.parse_args()
    if args.mode != "orca":
        parser.error("adapter requires --mode orca")
    if args.execute and not args.start_approved:
        parser.error("--execute requires recorded Owner Start approval")
    audit = f".planning/{args.plan_id}/orca"
    commands = [["orca", "orchestration", "run-create", "--objective", args.plan_id, "--json"]]
    plan = {
        "mode": "execute-plan" if args.execute else "apply" if args.apply else "dry-run",
        "run": {"owner_task": args.plan_id},
        "tasks": [{"phase": phase, "depends_on": PHASES[index - 1:index]} for index, phase in enumerate(PHASES)],
        "decision_tasks": ["Start", "Plan-to-Build", "Final Acceptance"],
        "audit": {"run_map": f"{audit}/run-map.json", "decision_packets": f"{audit}/decision-packets/"},
        "orca_calls": [] if not args.apply else commands,
        "planned_orca_commands": commands,
    }
    if args.apply:
        receipt = json.loads(subprocess.check_output(commands[0], text=True))
        plan["apply_receipt"] = receipt
        plan["warning"] = "Run created; task DAG/Dispatch remains explicit adapter follow-up."
    if args.execute:
        plan["warning"] = "Start approved; worktree/Dispatch commands require reviewed task specs and are not implicit."
    print(json.dumps(plan, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
