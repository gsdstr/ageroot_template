"""Comprehensive tests for safety-first comparison strategy and AgerootUpdateEngine."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ageroot_core import (
    AgerootUpdateEngine,
    ApplyOptions,
    ApplyOutcome,
    ComparisonResult,
    DeterministicNormalizer,
    DryRunOptions,
    DryRunReport,
    ManagedRegionParser,
    ResultClass,
    SnapshotStore,
    Strategy,
    SummaryReport,
    format_summary,
)
from ageroot_core.comparison import SimpleYamlHelper


class TestDeterministicNormalizer(unittest.TestCase):
    def test_crlf_and_trailing_whitespace(self):
        raw = "line 1   \r\nline 2\t\r\n\r\n\r\n\r\nline 3   \r\n"
        norm = DeterministicNormalizer.normalize_text(raw)
        self.assertEqual(norm, "line 1\nline 2\n\nline 3\n")

    def test_empty_string(self):
        self.assertEqual(DeterministicNormalizer.normalize_text(""), "")
        self.assertEqual(DeterministicNormalizer.normalize_text("   \r\n\n"), "")

    def test_generated_by_provenance_is_metadata_only(self):
        old = "<!-- generated-by: ageroot; template: 0.1.0; commit: abc1234; rendered-at: 2026-09-03T00:00:00Z -->\n# Title\n"
        new = "<!-- generated-by: ageroot; template: 0.1.1; commit: def5678; rendered-at: 2026-09-04T00:00:00Z -->\n# Title\n"
        self.assertEqual(DeterministicNormalizer.normalize_text(old), DeterministicNormalizer.normalize_text(new))


class TestSnapshotStore(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.store = SnapshotStore(self.root)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_write_and_read_snapshot_with_integrity(self):
        content = "hello world\n"
        sha = self.store.write_snapshot_atomic("sub/file.txt", content)
        self.assertEqual(sha, SnapshotStore.compute_sha256(content))

        # Read ok
        read_content, err = self.store.read_snapshot("sub/file.txt", sha)
        self.assertIsNone(err)
        self.assertEqual(read_content, content)

        # Corrupt check
        read_content, err = self.store.read_snapshot("sub/file.txt", "wrong_hash")
        self.assertEqual(err, "corrupt")
        self.assertIsNone(read_content)

        # Missing check
        read_content, err = self.store.read_snapshot("missing.txt", sha)
        self.assertEqual(err, "missing")
        self.assertIsNone(read_content)


class TestManagedRegionParser(unittest.TestCase):
    def test_valid_region_parsing(self):
        text = (
            "# Title\n\n"
            "<!-- caveman-begin -->\n"
            "caveman content\n"
            "<!-- caveman-end -->\n\n"
            "<!-- region:custom-user kind:user -->\n"
            "my custom user rules\n"
            "<!-- endregion:custom-user -->\n"
        )
        ok, segs, err = ManagedRegionParser.parse_structure(text)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertTrue(any(s["type"] == "region" and s["name"] == "caveman" for s in segs))
        self.assertTrue(any(s["type"] == "region" and s["name"] == "custom-user" for s in segs))

    def test_malformed_region_unpaired(self):
        text = "# Title\n<!-- caveman-begin -->\nno end marker"
        ok, segs, err = ManagedRegionParser.parse_structure(text)
        self.assertFalse(ok)
        self.assertIn("Unclosed region", err)

    def test_malformed_region_nested(self):
        text = (
            "<!-- caveman-begin -->\n"
            "<!-- rtk-begin -->\n"
            "<!-- rtk-end -->\n"
            "<!-- caveman-end -->\n"
        )
        ok, segs, err = ManagedRegionParser.parse_structure(text)
        self.assertFalse(ok)
        self.assertIn("Nested region", err)

    def test_region_merge_preserves_user_and_updates_generated(self):
        base = (
            "<!-- header-begin -->\nv1.0\n<!-- header-end -->\n"
            "<!-- user-notes-begin -->\nmy old note\n<!-- user-notes-end -->\n"
        )
        current = (
            "<!-- header-begin -->\nv1.0\n<!-- header-end -->\n"
            "<!-- user-notes-begin -->\nmy updated note\n<!-- user-notes-end -->\n"
        )
        new_render = (
            "<!-- header-begin -->\nv2.0\n<!-- header-end -->\n"
            "<!-- user-notes-begin -->\ndefault template note\n<!-- user-notes-end -->\n"
        )
        ok, merged, warnings, err = ManagedRegionParser.merge(current, new_render, base)
        self.assertTrue(ok)
        self.assertIn("v2.0", merged)
        self.assertIn("my updated note", merged)
        self.assertNotIn("default template note", merged)
        self.assertTrue(any("user-notes" in w for w in warnings))

    def test_concurrent_edit_in_generated_region_is_conflict(self):
        base = "<!-- header-begin -->\nv1.0\n<!-- header-end -->\n"
        current = "<!-- header-begin -->\nv1.0-custom-edit\n<!-- header-end -->\n"
        new_render = "<!-- header-begin -->\nv2.0\n<!-- header-end -->\n"
        ok, merged, warnings, err = ManagedRegionParser.merge(current, new_render, base)
        self.assertFalse(ok)
        self.assertIn("Concurrent edit", err)


class TestSimpleYamlHelper(unittest.TestCase):
    def test_yaml_parse_and_dump_roundtrip(self):
        yaml_content = (
            "schema: 1\n"
            "snapshots: enabled\n"
            "project:\n"
            "  name: my-project\n"
            "template:\n"
            "  name: ageroot\n"
            "  version: 0.1.0\n"
            "installed_at: '2026-09-03T00:00:00Z'\n"
            "managed_files:\n"
            "- path: AGENTS.md\n"
            "  strategy: generated\n"
            "  snapshot: .agents/snapshots/AGENTS.md\n"
            "  sha256: '7f6bedc46bceeaff0fded6d2ff233ee305853987585865a8f5d34cc3f702a947'\n"
            "- path: .agents/skills/\n"
            "  strategy: external-link\n"
        )
        parsed = SimpleYamlHelper.parse(yaml_content)
        self.assertEqual(parsed["schema"], 1)
        self.assertEqual(parsed["snapshots"], "enabled")
        self.assertEqual(parsed["project"]["name"], "my-project")
        self.assertEqual(len(parsed["managed_files"]), 2)
        self.assertEqual(parsed["managed_files"][0]["path"], "AGENTS.md")

        dumped = SimpleYamlHelper.dump(parsed)
        reparsed = SimpleYamlHelper.parse(dumped)
        self.assertEqual(parsed, reparsed)


class TestAgerootUpdateEngine(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.agents_dir = self.root / ".agents"
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.agents_dir / "ageroot.config.yaml"
        self.state_path = self.agents_dir / "ageroot.state.yaml"

        # Default config
        self.config_path.write_text("schema: 1\nsnapshots: enabled\n", encoding="utf-8")

        # Default empty state
        initial_state = {
            "schema": 1,
            "template": {"name": "ageroot", "version": "0.1.0", "source": "local", "commit": "abc1234"},
            "installed_at": "2026-09-03T00:00:00Z",
            "rendered_at": "2026-09-03T00:00:00Z",
            "managed_files": [],
        }
        self.state_path.write_text(SimpleYamlHelper.dump(initial_state), encoding="utf-8")

        self.engine = AgerootUpdateEngine(
            root_dir=self.root,
            config_path=self.config_path,
            state_path=self.state_path,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_clean_generated_dry_run_and_apply(self):
        rel_path = ".agents/AGENTS.md"
        base_text = "# Title\nInitial baseline\n"
        new_text = "# Title\nUpdated template\n"

        # Setup baseline
        target = self.root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(base_text, encoding="utf-8")
        sha = self.engine.snapshot_store.write_snapshot_atomic(rel_path, base_text)

        state = SimpleYamlHelper.parse(self.state_path.read_text(encoding="utf-8"))
        state["managed_files"] = [
            {"path": rel_path, "strategy": "generated", "snapshot": f".agents/snapshots/{rel_path}", "sha256": sha}
        ]
        self.state_path.write_text(SimpleYamlHelper.dump(state), encoding="utf-8")

        # 1. Dry run
        report = self.engine.dry_run({rel_path: new_text})
        self.assertTrue(report.is_eligible)
        self.assertEqual(report[rel_path].result_class, ResultClass.CHANGED)
        self.assertEqual(report[rel_path].strategy, Strategy.GENERATED)

        # 2. Apply
        outcome = self.engine.apply(report)
        self.assertTrue(outcome.success)
        self.assertIn(rel_path, outcome.applied_paths)

        # Verify workspace file updated
        self.assertEqual(target.read_text(encoding="utf-8"), DeterministicNormalizer.normalize_text(new_text))

        # Verify snapshot updated
        new_snap, snap_err = self.engine.snapshot_store.read_snapshot(rel_path)
        self.assertIsNone(snap_err)
        self.assertEqual(new_snap, DeterministicNormalizer.normalize_text(new_text))

        # Verify state updated with new sha
        updated_state = SimpleYamlHelper.parse(self.state_path.read_text(encoding="utf-8"))
        entry = next(e for e in updated_state["managed_files"] if e["path"] == rel_path)
        expected_sha = SnapshotStore.compute_sha256(DeterministicNormalizer.normalize_text(new_text))
        self.assertEqual(entry["sha256"], expected_sha)

        # Subsequent dry-run is UNCHANGED
        report_subsequent = self.engine.dry_run({rel_path: new_text})
        self.assertEqual(report_subsequent[rel_path].result_class, ResultClass.UNCHANGED)
        self.assertTrue(report_subsequent.is_eligible)

    def test_planning_path_excluded_from_dry_run_and_reporting(self):
        plan_path = ".planning/feature/task_plan.md"
        renders = {
            plan_path: "# Task Plan\nShould be excluded\n",
            ".agents/AGENTS.md": "# Clean Agents\n",
        }
        report = self.engine.dry_run(renders)
        self.assertTrue(report.is_eligible)
        self.assertTrue(report[plan_path].excluded)
        self.assertEqual(report[plan_path].result_class, ResultClass.UNCHANGED)

        summary = format_summary(report)
        self.assertNotIn(".planning", summary)

        outcome = self.engine.apply(report)
        self.assertTrue(outcome.success)
        self.assertNotIn(plan_path, outcome.applied_paths)
        self.assertFalse((self.root / plan_path).exists())

    def test_strategy_autodetection_for_new_paths(self):
        # Path with region markers -> MANAGED_REGIONS
        region_content = (
            "# Agent Config\n\n"
            "<!-- region:caveman kind:generated -->\n"
            "caveman content\n"
            "<!-- endregion:caveman -->\n"
        )
        plain_content = "# Plain Doc\nNo markers here\n"

        renders = {
            ".agents/AGENTS.md": region_content,
            ".agents/README.md": plain_content,
        }
        report = self.engine.dry_run(renders)
        self.assertEqual(report[".agents/AGENTS.md"].strategy, Strategy.MANAGED_REGIONS)
        self.assertEqual(report[".agents/README.md"].strategy, Strategy.GENERATED)

    def test_managed_regions_merge_and_conflict(self):
        rel_path = ".agents/AGENTS.md"
        base_text = (
            "<!-- header-begin -->\nv1.0\n<!-- header-end -->\n"
            "<!-- user-notes-begin -->\nbase note\n<!-- user-notes-end -->\n"
        )
        curr_text = (
            "<!-- header-begin -->\nv1.0\n<!-- header-end -->\n"
            "<!-- user-notes-begin -->\ncustom user note\n<!-- user-notes-end -->\n"
        )
        new_render = (
            "<!-- header-begin -->\nv2.0\n<!-- header-end -->\n"
            "<!-- user-notes-begin -->\ndefault template note\n<!-- user-notes-end -->\n"
        )

        target = self.root / rel_path
        target.write_text(curr_text, encoding="utf-8")
        sha = self.engine.snapshot_store.write_snapshot_atomic(rel_path, base_text)

        state = SimpleYamlHelper.parse(self.state_path.read_text(encoding="utf-8"))
        state["managed_files"] = [
            {"path": rel_path, "strategy": "managed-regions", "snapshot": f".agents/snapshots/{rel_path}", "sha256": sha}
        ]
        self.state_path.write_text(SimpleYamlHelper.dump(state), encoding="utf-8")

        # 1. Clean merge preserving user region
        report = self.engine.dry_run({rel_path: new_render})
        self.assertEqual(report[rel_path].result_class, ResultClass.REGION_MERGE)
        self.assertTrue(report.is_eligible)
        self.assertIn("custom user note", report[rel_path].proposed_content)
        self.assertIn("v2.0", report[rel_path].proposed_content)

        outcome = self.engine.apply(report)
        self.assertTrue(outcome.success)
        self.assertIn("custom user note", target.read_text(encoding="utf-8"))
        self.assertIn("v2.0", target.read_text(encoding="utf-8"))

        # 2. Concurrent edit in generated region causes CONFLICT
        conflict_curr = (
            "<!-- header-begin -->\nv2.0-local-hack\n<!-- header-end -->\n"
            "<!-- user-notes-begin -->\ncustom user note\n<!-- user-notes-end -->\n"
        )
        target.write_text(conflict_curr, encoding="utf-8")
        v3_render = (
            "<!-- header-begin -->\nv3.0\n<!-- header-end -->\n"
            "<!-- user-notes-begin -->\ndefault\n<!-- user-notes-end -->\n"
        )
        report_conflict = self.engine.dry_run({rel_path: v3_render})
        self.assertEqual(report_conflict[rel_path].result_class, ResultClass.CONFLICT)
        self.assertFalse(report_conflict.is_eligible)

        outcome_conflict = self.engine.apply(report_conflict)
        self.assertFalse(outcome_conflict.success)
        self.assertIn("ineligible", outcome_conflict.error)

    def test_workspace_drift_detection_modified(self):
        rel_path = ".agents/AGENTS.md"
        base_text = "# Clean\n"
        target = self.root / rel_path
        target.write_text(base_text, encoding="utf-8")
        sha = self.engine.snapshot_store.write_snapshot_atomic(rel_path, base_text)

        state = SimpleYamlHelper.parse(self.state_path.read_text(encoding="utf-8"))
        state["managed_files"] = [
            {"path": rel_path, "strategy": "generated", "snapshot": f".agents/snapshots/{rel_path}", "sha256": sha}
        ]
        self.state_path.write_text(SimpleYamlHelper.dump(state), encoding="utf-8")

        report = self.engine.dry_run({rel_path: "# Clean\nUpdated\n"})
        self.assertTrue(report.is_eligible)

        # Simulate concurrent drift in workspace before apply
        target.write_text("# Concurrent Out-of-Band Modification\n", encoding="utf-8")

        outcome = self.engine.apply(report)
        self.assertFalse(outcome.success)
        self.assertIn("Workspace drift detected", outcome.error)
        self.assertIn("file modified after dry-run", outcome.error)
        # Verify file on disk was NOT overwritten
        self.assertEqual(target.read_text(encoding="utf-8"), "# Concurrent Out-of-Band Modification\n")

    def test_workspace_drift_detection_created(self):
        rel_path = ".agents/NEW.md"
        # File did not exist at dry-run
        report = self.engine.dry_run({rel_path: "# Brand new\n"})
        self.assertTrue(report.is_eligible)

        # Simulate concurrent creation before apply
        target = self.root / rel_path
        target.write_text("# Conflicting creation\n", encoding="utf-8")

        outcome = self.engine.apply(report)
        self.assertFalse(outcome.success)
        self.assertIn("Workspace drift detected", outcome.error)
        self.assertIn("file was created after dry-run", outcome.error)

    def test_workspace_drift_detection_deleted(self):
        rel_path = ".agents/AGENTS.md"
        target = self.root / rel_path
        target.write_text("# Base\n", encoding="utf-8")
        sha = self.engine.snapshot_store.write_snapshot_atomic(rel_path, "# Base\n")

        state = SimpleYamlHelper.parse(self.state_path.read_text(encoding="utf-8"))
        state["managed_files"] = [
            {"path": rel_path, "strategy": "generated", "snapshot": f".agents/snapshots/{rel_path}", "sha256": sha}
        ]
        self.state_path.write_text(SimpleYamlHelper.dump(state), encoding="utf-8")

        report = self.engine.dry_run({rel_path: "# Base\nUpdate\n"})
        self.assertTrue(report.is_eligible)

        # Simulate out-of-band deletion before apply
        target.unlink()

        outcome = self.engine.apply(report)
        self.assertFalse(outcome.success)
        self.assertIn("Workspace drift detected", outcome.error)
        self.assertIn("file was deleted after dry-run", outcome.error)

    def test_snapshots_disabled_policy(self):
        # Set snapshots: disabled in config
        self.config_path.write_text("schema: 1\nsnapshots: disabled\n", encoding="utf-8")
        engine = AgerootUpdateEngine(self.root, self.config_path, self.state_path)

        rel_path = ".agents/PREFERENCES.md"
        target = self.root / rel_path
        target.write_text("pref 1\n", encoding="utf-8")

        report = engine.dry_run({rel_path: "pref 2\n"})
        self.assertEqual(report[rel_path].result_class, ResultClass.UNVERIFIED)
        self.assertTrue(report.is_eligible)

        outcome = engine.apply(report)
        self.assertTrue(outcome.success)
        self.assertEqual(target.read_text(encoding="utf-8"), "pref 2\n")

        # Snapshot file must NOT have been written
        snap_file = engine.snapshot_store.get_snapshot_path(rel_path)
        self.assertFalse(snap_file.exists())

    def test_snapshot_corruption_and_one_time_override(self):
        rel_path = ".agents/AGENTS.md"
        base_text = "# Baseline\n"
        target = self.root / rel_path
        target.write_text(base_text, encoding="utf-8")

        # Write corrupted snapshot content
        self.engine.snapshot_store.write_snapshot_atomic(rel_path, "corrupted content")
        expected_sha = SnapshotStore.compute_sha256(base_text)

        state = SimpleYamlHelper.parse(self.state_path.read_text(encoding="utf-8"))
        state["managed_files"] = [
            {"path": rel_path, "strategy": "generated", "snapshot": f".agents/snapshots/{rel_path}", "sha256": expected_sha}
        ]
        self.state_path.write_text(SimpleYamlHelper.dump(state), encoding="utf-8")

        # 1. Normal dry run -> BLOCKED
        report = self.engine.dry_run({rel_path: "# New Render\n"})
        self.assertEqual(report[rel_path].result_class, ResultClass.BLOCKED)
        self.assertFalse(report.is_eligible)

        outcome = self.engine.apply(report)
        self.assertFalse(outcome.success)
        self.assertIn("ineligible status 'blocked'", outcome.error)

        # 2. One-time unverified override -> UNVERIFIED, eligible
        report_override = self.engine.dry_run(
            {rel_path: "# New Render\n"},
            options=DryRunOptions(one_time_unverified_override=True),
        )
        self.assertEqual(report_override[rel_path].result_class, ResultClass.UNVERIFIED)
        self.assertTrue(report_override.is_eligible)

        outcome_override = self.engine.apply(report_override)
        self.assertTrue(outcome_override.success)

    def test_external_link_validation_and_persistence(self):
        rel_link = ".agents/skills/planning"
        target_dir = self.root / ".skills-manager" / "planning"
        target_dir.mkdir(parents=True, exist_ok=True)

        link_path = self.root / rel_link
        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(target_dir)

        state = SimpleYamlHelper.parse(self.state_path.read_text(encoding="utf-8"))
        state["managed_files"] = [
            {"path": rel_link, "strategy": "external-link", "target": str(target_dir)}
        ]
        self.state_path.write_text(SimpleYamlHelper.dump(state), encoding="utf-8")

        # Dry-run without including link in renders: must validate link and not treat as deletion
        report = self.engine.dry_run({})
        self.assertTrue(report.is_eligible)
        self.assertEqual(report[rel_link].result_class, ResultClass.UNCHANGED)
        self.assertEqual(report[rel_link].strategy, Strategy.EXTERNAL_LINK)

        # If link is broken:
        link_path.unlink()
        broken_target = self.root / ".nonexistent"
        link_path.symlink_to(broken_target)

        report_broken = self.engine.dry_run({})
        self.assertEqual(report_broken[rel_link].result_class, ResultClass.INVALID_LINK)
        self.assertFalse(report_broken.is_eligible)

        outcome = self.engine.apply(report_broken)
        self.assertFalse(outcome.success)
        self.assertIn("invalid-link", outcome.error)

    def test_deletion_escalation_clean_and_modified(self):
        rel_path = ".agents/old_file.md"
        base_text = "original content\n"
        target = self.root / rel_path
        target.write_text(base_text, encoding="utf-8")
        sha = self.engine.snapshot_store.write_snapshot_atomic(rel_path, base_text)

        state = SimpleYamlHelper.parse(self.state_path.read_text(encoding="utf-8"))
        state["managed_files"] = [
            {"path": rel_path, "strategy": "generated", "snapshot": f".agents/snapshots/{rel_path}", "sha256": sha}
        ]
        self.state_path.write_text(SimpleYamlHelper.dump(state), encoding="utf-8")

        # 1. Clean deletion absent from renders -> DELETION_PENDING
        report = self.engine.dry_run({})
        self.assertEqual(report[rel_path].result_class, ResultClass.DELETION_PENDING)
        self.assertFalse(report.is_eligible)  # Blocks ordinary apply

        # Ordinary apply fails
        outcome = self.engine.apply(report)
        self.assertFalse(outcome.success)
        self.assertIn("deletion pending", outcome.error)

        # Apply with explicit deletion confirmation succeeds
        report_allow = self.engine.dry_run({}, options=DryRunOptions(allow_deletions=True))
        self.assertTrue(report_allow.is_eligible)
        outcome_allow = self.engine.apply(report_allow, options=ApplyOptions(allow_deletions=True))
        self.assertTrue(outcome_allow.success)
        self.assertIn(rel_path, outcome_allow.deleted_paths)
        self.assertFalse(target.exists())

        # 2. Locally modified file absent from renders -> BLOCKED
        target.write_text("locally modified content\n", encoding="utf-8")
        sha_orig = self.engine.snapshot_store.write_snapshot_atomic(rel_path, base_text)
        state["managed_files"] = [
            {"path": rel_path, "strategy": "generated", "snapshot": f".agents/snapshots/{rel_path}", "sha256": sha_orig}
        ]
        self.state_path.write_text(SimpleYamlHelper.dump(state), encoding="utf-8")

        report_modified = self.engine.dry_run({}, options=DryRunOptions(allow_deletions=True))
        self.assertEqual(report_modified[rel_path].result_class, ResultClass.BLOCKED)
        self.assertFalse(report_modified.is_eligible)
        self.assertIn("Locally modified", report_modified[rel_path].reason)

        outcome_mod = self.engine.apply(report_modified, options=ApplyOptions(allow_deletions=True))
        self.assertFalse(outcome_mod.success)
        self.assertIn("ineligible status 'blocked'", outcome_mod.error)
        self.assertTrue(target.exists())

    def test_staging_cleanup_and_transactional_rollback(self):
        rel_path = ".agents/AGENTS.md"
        base_text = "# Baseline\n"
        target = self.root / rel_path
        target.write_text(base_text, encoding="utf-8")
        sha = self.engine.snapshot_store.write_snapshot_atomic(rel_path, base_text)

        state = SimpleYamlHelper.parse(self.state_path.read_text(encoding="utf-8"))
        state["managed_files"] = [
            {"path": rel_path, "strategy": "generated", "snapshot": f".agents/snapshots/{rel_path}", "sha256": sha}
        ]
        self.state_path.write_text(SimpleYamlHelper.dump(state), encoding="utf-8")

        report = self.engine.dry_run({rel_path: "# Updated\n"})
        outcome = self.engine.apply(report)
        self.assertTrue(outcome.success)

        # Verify no .staging-* folders remain in .agents
        staging_dirs = [d for d in self.agents_dir.iterdir() if d.is_dir() and d.name.startswith(".staging-")]
        self.assertEqual(len(staging_dirs), 0)

    def test_transactional_rollback_on_apply_failure(self):
        rel_1 = ".agents/AGENTS.md"
        rel_2 = ".agents/SETUP.md"
        content_1_orig = "# Agents v1\n"
        content_2_orig = "# Setup v1\n"

        target_1 = self.root / rel_1
        target_2 = self.root / rel_2
        target_1.write_text(content_1_orig, encoding="utf-8")
        target_2.write_text(content_2_orig, encoding="utf-8")

        sha_1 = self.engine.snapshot_store.write_snapshot_atomic(rel_1, content_1_orig)
        sha_2 = self.engine.snapshot_store.write_snapshot_atomic(rel_2, content_2_orig)

        state = SimpleYamlHelper.parse(self.state_path.read_text(encoding="utf-8"))
        state["managed_files"] = [
            {"path": rel_1, "strategy": "generated", "snapshot": f".agents/snapshots/{rel_1}", "sha256": sha_1},
            {"path": rel_2, "strategy": "generated", "snapshot": f".agents/snapshots/{rel_2}", "sha256": sha_2},
        ]
        self.state_path.write_text(SimpleYamlHelper.dump(state), encoding="utf-8")

        renders = {
            rel_1: "# Agents v2 Updated\n",
            rel_2: "# Setup v2 Updated\n",
        }
        report = self.engine.dry_run(renders)
        self.assertTrue(report.is_eligible)

        # Inject a failure during file replace for the second file
        original_replace = Path.replace
        def mock_replace(p_self, target):
            if "SETUP.md" in str(target) and ".staging-" not in str(target):
                raise OSError("Simulated disk error during atomic move")
            return original_replace(p_self, target)

        Path.replace = mock_replace
        try:
            outcome = self.engine.apply(report)
        finally:
            Path.replace = original_replace

        self.assertFalse(outcome.success)
        self.assertIn("Simulated disk error", outcome.error)

        # AGENTS.md must have been rolled back to content_1_orig!
        self.assertEqual(target_1.read_text(encoding="utf-8"), content_1_orig)
        self.assertEqual(target_2.read_text(encoding="utf-8"), content_2_orig)

        # No staging directory should remain
        staging_dirs = [d for d in self.agents_dir.iterdir() if d.is_dir() and d.name.startswith(".staging-")]
        self.assertEqual(len(staging_dirs), 0)

    def test_format_summary_tiered_reporting(self):
        results = [
            ComparisonResult(
                path=".agents/AGENTS.md",
                strategy=Strategy.GENERATED,
                result_class=ResultClass.CHANGED,
            ),
            ComparisonResult(
                path=".agents/SETUP.md",
                strategy=Strategy.GENERATED,
                result_class=ResultClass.UNCHANGED,
            ),
            ComparisonResult(
                path=".agents/rules.md",
                strategy=Strategy.GENERATED,
                result_class=ResultClass.CONFLICT,
                reason="Concurrent edits",
                diff="some diff",
            ),
        ]
        summary = SummaryReport.format_summary(results)
        self.assertIn("**Atomic Apply Status**: BLOCKED", summary)
        self.assertIn("- Changed: 1", summary)
        self.assertIn("- Unchanged: 1", summary)
        self.assertIn("- Conflicts: 1", summary)
        self.assertIn("Attention Required", summary)
        self.assertIn(".agents/rules.md", summary)
        self.assertIn("Full Diff Access", summary)


if __name__ == "__main__":
    unittest.main()
