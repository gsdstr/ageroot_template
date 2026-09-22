#!/usr/bin/env python3
"""Offline structural validation for neutral orchestration templates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROLES = {"Owner", "Coordinator", "Researcher", "Author", "Builder", "Reviewer", "Integrator"}
PHASE_IDS = ("intake", "research-planning", "implementation", "review-integration")
PHASE_FIELDS = {
    "metadata",
    "participating_roles",
    "entry_criteria",
    "exit_criteria",
    "handoff_artifacts",
    "budget_slice",
    "escalation_gates",
}
BUDGET_LIMITS = {"iterations": 12, "agent_turns": 30, "model_capability_units": 100, "wall_clock_minutes": 120}
REQUIRED_ARTIFACTS = {"task-brief", "task-spec", "implementation-manifest", "release-packet", "change-request"}


class ValidationError(ValueError):
    pass


def load_json_yaml(path: Path) -> dict[str, Any]:
    """Read JSON-compatible YAML without a third-party YAML dependency."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValidationError(f"{path}: use JSON-compatible YAML: {error.msg}") from error
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: top-level value must be an object")
    return value


def validate_manifest(manifest: dict[str, Any]) -> None:
    required = {"version", "templates", "lifecycle_map", "handoff_schema"}
    missing = required - manifest.keys()
    if missing:
        raise ValidationError(f"manifest missing: {', '.join(sorted(missing))}")
    if manifest["version"] != "1.0.0":
        raise ValidationError("manifest version must be 1.0.0")
    templates = manifest["templates"]
    if not isinstance(templates, list):
        raise ValidationError("manifest templates must be an array")
    ids = set()
    for template in templates:
        if not isinstance(template, dict) or set(template) != {"id", "path", "schema"}:
            raise ValidationError("each manifest template needs only id, path, and schema")
        if template["id"] not in PHASE_IDS:
            raise ValidationError(f"unknown template id: {template['id']}")
        if template["schema"] != "schemas/phase-template.schema.json":
            raise ValidationError("phase template must use phase-template.schema.json")
        ids.add(template["id"])
    if ids != set(PHASE_IDS):
        raise ValidationError("manifest must declare every canonical phase exactly once")
    if manifest["handoff_schema"] != "schemas/handoff.schema.json":
        raise ValidationError("manifest handoff_schema must reference handoff.schema.json")
    for schema in (
        "schemas/manifest.schema.json",
        "schemas/phase-template.schema.json",
        "schemas/lifecycle-map.schema.json",
        "schemas/handoff.schema.json",
    ):
        if not (ROOT / schema).is_file():
            raise ValidationError(f"required schema is missing: {schema}")


def validate_phase(phase: dict[str, Any]) -> None:
    missing = PHASE_FIELDS - phase.keys()
    if missing:
        raise ValidationError(f"phase missing: {', '.join(sorted(missing))}")
    unknown = set(phase["participating_roles"]) - ROLES
    if unknown:
        raise ValidationError(f"unknown participating role: {', '.join(sorted(unknown))}")
    metadata = phase["metadata"]
    if not isinstance(metadata, dict) or {"id", "phase_index", "name", "summary"} - metadata.keys():
        raise ValidationError("phase metadata must include id, phase_index, name, summary")
    if metadata["id"] not in PHASE_IDS:
        raise ValidationError(f"unknown phase id: {metadata['id']}")
    if not isinstance(phase["handoff_artifacts"], list):
        raise ValidationError("phase handoff_artifacts must be an array")
    if set(phase["budget_slice"]) != set(BUDGET_LIMITS):
        raise ValidationError("phase budget_slice must declare every budget dimension")
    if any(not isinstance(value, int) or value < 0 for value in phase["budget_slice"].values()):
        raise ValidationError("phase budget_slice values must be non-negative integers")


def validate_handoff(handoff: dict[str, Any], source_roles: set[str], target_roles: set[str]) -> None:
    required = {"id", "from_phase", "to_phase", "producer_role", "consumer_role", "artifact_schema"}
    missing = required - handoff.keys()
    if missing:
        raise ValidationError(f"handoff missing: {', '.join(sorted(missing))}")
    if handoff["producer_role"] not in source_roles:
        raise ValidationError("handoff producer_role is absent from source phase")
    if handoff["consumer_role"] not in target_roles:
        raise ValidationError("handoff consumer_role is absent from target phase")


def validate_budget(phases: dict[str, dict[str, Any]]) -> None:
    totals = {dimension: sum(phase["budget_slice"][dimension] for phase in phases.values()) for dimension in BUDGET_LIMITS}
    for dimension, limit in BUDGET_LIMITS.items():
        if totals[dimension] > limit:
            raise ValidationError(f"budget slices exceed {dimension} limit")


def validate_lifecycle(lifecycle: dict[str, Any]) -> None:
    if lifecycle.get("phase_order") != list(PHASE_IDS):
        raise ValidationError("lifecycle phase_order must match canonical order")
    gate_names = {gate.get("name") for gate in lifecycle.get("owner_gates", []) if isinstance(gate, dict)}
    if {"Start", "Plan-to-Build", "Final Acceptance"} - gate_names:
        raise ValidationError("lifecycle is missing a required Owner gate")
    if not lifecycle.get("loopbacks"):
        raise ValidationError("lifecycle must declare loopbacks")


def validate_reviewer_independence(phase: dict[str, Any]) -> None:
    if {"Builder", "Reviewer"} - set(phase["participating_roles"]):
        raise ValidationError("implementation phase must include Builder and Reviewer")
    if phase["exit_criteria"].get("reviewer_independence") is not True:
        raise ValidationError("implementation phase must require reviewer independence")


def load_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValidationError(f"{path}: missing opening frontmatter marker")
    try:
        raw, _ = text[4:].split("\n---\n", 1)
    except ValueError as error:
        raise ValidationError(f"{path}: missing closing frontmatter marker") from error
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValidationError(f"{path}: frontmatter must be JSON-compatible YAML: {error.msg}") from error
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: frontmatter must be an object")
    return value


def validate_package() -> None:
    manifest = load_json_yaml(ROOT / "manifest.yaml")
    validate_manifest(manifest)
    phases: dict[str, dict[str, Any]] = {}
    for template in manifest["templates"]:
        path = ROOT / template["path"]
        if not path.is_file():
            raise ValidationError(f"manifest phase is missing: {template['path']}")
        phase = load_frontmatter(path)
        validate_phase(phase)
        phase_id = phase["metadata"]["id"]
        if phase_id != template["id"]:
            raise ValidationError(f"{path}: metadata id does not match manifest")
        phases[phase_id] = phase
    if set(phases) != set(PHASE_IDS):
        raise ValidationError("package must contain every canonical phase")
    validate_reviewer_independence(phases["implementation"])
    validate_budget(phases)
    artifacts = set()
    for phase in phases.values():
        for handoff in phase["handoff_artifacts"]:
            source = phases.get(handoff.get("from_phase"))
            target = phases.get(handoff.get("to_phase"))
            if source is None or target is None:
                raise ValidationError("handoff references an unknown phase")
            validate_handoff(handoff, set(source["participating_roles"]), set(target["participating_roles"]))
            artifacts.add(handoff["id"])
    if artifacts != REQUIRED_ARTIFACTS:
        raise ValidationError("package must declare exactly the five canonical handoff artifacts")
    lifecycle_path = ROOT / manifest["lifecycle_map"]["path"]
    lifecycle = load_frontmatter(lifecycle_path)
    validate_lifecycle(lifecycle)


def schema_smoke_test() -> None:
    validate_phase({
        "metadata": {"id": "intake", "phase_index": 1, "name": "Intake", "summary": "x"},
        "participating_roles": ["Owner", "Coordinator"],
        "entry_criteria": {}, "exit_criteria": {}, "handoff_artifacts": [],
        "budget_slice": {"iterations": 0, "agent_turns": 0, "model_capability_units": 0, "wall_clock_minutes": 0}, "escalation_gates": [],
    })
    try:
        validate_phase({"participating_roles": ["Unknown"]})
    except ValidationError:
        return
    raise ValidationError("smoke test did not reject invalid phase")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifest.yaml")
    parser.add_argument("--schema-smoke-test", action="store_true")
    parser.add_argument("--package", action="store_true")
    args = parser.parse_args()
    validate_manifest(load_json_yaml(args.manifest))
    if args.schema_smoke_test:
        schema_smoke_test()
    if args.package:
        validate_package()
    print("orchestration validation: PASS")


if __name__ == "__main__":
    main()
