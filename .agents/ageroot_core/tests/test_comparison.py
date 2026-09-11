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
    RegionMergeResult,
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
    def test_has_region_markers(self):
        # Type 1: VS Code #region in various comment styles
        self.assertTrue(ManagedRegionParser.has_region_markers("<!-- #region foo -->"))
        self.assertTrue(ManagedRegionParser.has_region_markers("<!-- #region foo kind:generated -->"))
        self.assertTrue(ManagedRegionParser.has_region_markers("<!-- #endregion -->"))
        self.assertTrue(ManagedRegionParser.has_region_markers("<!-- #endregion foo -->"))
        self.assertTrue(ManagedRegionParser.has_region_markers("# #region py_block"))
        self.assertTrue(ManagedRegionParser.has_region_markers("# #endregion"))
        self.assertTrue(ManagedRegionParser.has_region_markers("// #region js_block"))
        self.assertTrue(ManagedRegionParser.has_region_markers("// #endregion"))

        # Type 1: Legacy aliases
        self.assertTrue(ManagedRegionParser.has_region_markers("<!-- region:old-block kind:user -->"))
        self.assertTrue(ManagedRegionParser.has_region_markers("<!-- endregion:old-block -->"))

        # Type 3: Skill blocks
        self.assertTrue(ManagedRegionParser.has_region_markers("<!-- caveman-begin -->"))
        self.assertTrue(ManagedRegionParser.has_region_markers("<!-- caveman-end -->"))
        self.assertTrue(ManagedRegionParser.has_region_markers("# rtk-begin"))
        self.assertTrue(ManagedRegionParser.has_region_markers("# rtk-end"))
        self.assertTrue(ManagedRegionParser.has_region_markers("// custom-begin"))
        self.assertTrue(ManagedRegionParser.has_region_markers("// custom-end"))

        # Type 2: Metadata directives (must NOT match as region markers)
        self.assertFalse(ManagedRegionParser.has_region_markers(
            "<!-- generated-by: ageroot; template: 0.1.0; commit: abc; rendered-at: 2026-09-03T00:00:00Z -->"
        ))
        self.assertFalse(ManagedRegionParser.has_region_markers("# generated-by: ageroot"))
        self.assertFalse(ManagedRegionParser.has_region_markers("// generated-by: ageroot"))

        # Plain text and empty
        self.assertFalse(ManagedRegionParser.has_region_markers("# Just a normal markdown heading"))
        self.assertFalse(ManagedRegionParser.has_region_markers("def hello(): pass"))
        self.assertFalse(ManagedRegionParser.has_region_markers(""))

    def test_type1_clean_generated_update(self):
        base = "<!-- #region header kind:generated -->\nv1.0\n<!-- #endregion header -->\n"
        current = "<!-- #region header kind:generated -->\nv1.0\n<!-- #endregion header -->\n"
        new_render = "<!-- #region header kind:generated -->\nv2.0\n<!-- #endregion header -->\n"
        res = ManagedRegionParser.merge(current, new_render, base)
        self.assertEqual(res.result_class, ResultClass.REGION_MERGE)
        self.assertIn("v2.0", res.merged_text)
        self.assertNotIn("v1.0", res.merged_text)
        self.assertEqual(res.warnings, [])

    def test_type1_user_preservation_and_warning(self):
        base = (
            "<!-- #region header kind:generated -->\nv1.0\n<!-- #endregion -->\n"
            "<!-- #region notes kind:user -->\nbase notes\n<!-- #endregion -->\n"
        )
        current = (
            "<!-- #region header kind:generated -->\nv1.0\n<!-- #endregion -->\n"
            "<!-- #region notes kind:user -->\nmy custom user notes\n<!-- #endregion -->\n"
        )
        new_render = (
            "<!-- #region header kind:generated -->\nv2.0\n<!-- #endregion -->\n"
            "<!-- #region notes kind:user -->\ndefault template notes\n<!-- #endregion -->\n"
        )
        res = ManagedRegionParser.merge(current, new_render, base)
        self.assertEqual(res.result_class, ResultClass.REGION_MERGE)
        self.assertIn("v2.0", res.merged_text)
        self.assertIn("my custom user notes", res.merged_text)
        self.assertNotIn("default template notes", res.merged_text)
        self.assertTrue(any("notes" in w and "local modifications" in w for w in res.warnings))

    def test_type1_default_kind_is_user(self):
        # Kind omitted -> defaults to user
        base = (
            "<!-- #region header kind:generated -->\nv1.0\n<!-- #endregion -->\n"
            "<!-- #region custom -->\nbase custom\n<!-- #endregion -->\n"
        )
        current = (
            "<!-- #region header kind:generated -->\nv1.0\n<!-- #endregion -->\n"
            "<!-- #region custom -->\nmy custom edit\n<!-- #endregion -->\n"
        )
        new_render = (
            "<!-- #region header kind:generated -->\nv2.0\n<!-- #endregion -->\n"
            "<!-- #region custom -->\ntemplate default\n<!-- #endregion -->\n"
        )
        res = ManagedRegionParser.merge(current, new_render, base)
        self.assertEqual(res.result_class, ResultClass.REGION_MERGE)
        self.assertIn("my custom edit", res.merged_text)
        self.assertNotIn("template default", res.merged_text)
        self.assertIn("v2.0", res.merged_text)

    def test_type1_anonymous_and_named_closing_tags(self):
        # Anonymous closing tag pops LIFO
        anon_text = "<!-- #region block1 -->\ncontent1\n<!-- #endregion -->\n"
        res_anon = ManagedRegionParser.merge(anon_text, anon_text)
        self.assertEqual(res_anon.result_class, ResultClass.UNCHANGED)

        # Named closing tag matches
        named_text = "<!-- #region block2 -->\ncontent2\n<!-- #endregion block2 -->\n"
        res_named = ManagedRegionParser.merge(named_text, named_text)
        self.assertEqual(res_named.result_class, ResultClass.UNCHANGED)

    def test_type1_multi_comment_prefixes(self):
        # Hash comment style (# #region)
        py_base = "# #region config kind:generated\nv1 = True\n# #endregion config\n"
        py_curr = "# #region config kind:generated\nv1 = True\n# #endregion config\n"
        py_new = "# #region config kind:generated\nv2 = True\n# #endregion config\n"
        res_py = ManagedRegionParser.merge(py_curr, py_new, py_base)
        self.assertEqual(res_py.result_class, ResultClass.REGION_MERGE)
        self.assertIn("v2 = True", res_py.merged_text)

        # Slash comment style (// #region)
        js_base = "// #region config kind:generated\nconst v = 1;\n// #endregion\n"
        js_curr = "// #region config kind:generated\nconst v = 1;\n// #endregion\n"
        js_new = "// #region config kind:generated\nconst v = 2;\n// #endregion\n"
        res_js = ManagedRegionParser.merge(js_curr, js_new, js_base)
        self.assertEqual(res_js.result_class, ResultClass.REGION_MERGE)
        self.assertIn("const v = 2;", res_js.merged_text)

    def test_type1_legacy_aliases(self):
        base = "<!-- region:sec kind:generated -->\nold\n<!-- endregion:sec -->\n"
        curr = "<!-- region:sec kind:generated -->\nold\n<!-- endregion:sec -->\n"
        new_render = "<!-- region:sec kind:generated -->\nnew\n<!-- endregion:sec -->\n"
        res = ManagedRegionParser.merge(curr, new_render, base)
        self.assertEqual(res.result_class, ResultClass.REGION_MERGE)
        self.assertIn("new", res.merged_text)

    def test_type2_metadata_directive_normalization(self):
        # Standalone generated-by is treated as unmanaged metadata and normalized
        meta1 = "<!-- generated-by: ageroot; template: 0.1.0; commit: abc; rendered-at: 2026-09-03T00:00:00Z -->\n# Title\n"
        meta2 = "<!-- generated-by: ageroot; template: 0.2.0; commit: def; rendered-at: 2026-09-04T00:00:00Z -->\n# Title\n"
        self.assertEqual(
            DeterministicNormalizer.normalize_text(meta1),
            DeterministicNormalizer.normalize_text(meta2),
        )
        res = ManagedRegionParser.merge(meta1, meta2)
        self.assertEqual(res.result_class, ResultClass.UNCHANGED)

    def test_type3_skill_blocks_user_ownership(self):
        # caveman, rtk, and custom skill blocks are always kind:user
        base = (
            "<!-- #region header kind:generated -->\nv1.0\n<!-- #endregion -->\n"
            "<!-- caveman-begin -->\nterse mode\n<!-- caveman-end -->\n"
            "<!-- rtk-begin -->\nrtk rules\n<!-- rtk-end -->\n"
        )
        curr = (
            "<!-- #region header kind:generated -->\nv1.0\n<!-- #endregion -->\n"
            "<!-- caveman-begin -->\nuser customized caveman\n<!-- caveman-end -->\n"
            "<!-- rtk-begin -->\nuser customized rtk\n<!-- rtk-end -->\n"
        )
        new_render = (
            "<!-- #region header kind:generated -->\nv2.0\n<!-- #endregion -->\n"
            "<!-- caveman-begin -->\ndefault upstream caveman\n<!-- caveman-end -->\n"
            "<!-- rtk-begin -->\ndefault upstream rtk\n<!-- rtk-end -->\n"
        )
        res = ManagedRegionParser.merge(curr, new_render, base)
        self.assertEqual(res.result_class, ResultClass.REGION_MERGE)
        self.assertIn("v2.0", res.merged_text)
        self.assertIn("user customized caveman", res.merged_text)
        self.assertIn("user customized rtk", res.merged_text)
        self.assertNotIn("default upstream caveman", res.merged_text)
        self.assertNotIn("default upstream rtk", res.merged_text)

    def test_structural_error_unclosed_region(self):
        text = "# Title\n<!-- #region unclosed -->\nno end marker\n"
        res = ManagedRegionParser.merge(text, text)
        self.assertEqual(res.result_class, ResultClass.BLOCKED)
        self.assertIn("Unclosed region", res.reason)

    def test_structural_error_unmatched_closing_tag(self):
        named_text = "<!-- #endregion orphan -->\n"
        res_named = ManagedRegionParser.merge(named_text, named_text)
        self.assertEqual(res_named.result_class, ResultClass.BLOCKED)
        self.assertIn("Unmatched closing marker", res_named.reason)

        anon_text = "<!-- #endregion -->\n"
        res_anon = ManagedRegionParser.merge(anon_text, anon_text)
        self.assertEqual(res_anon.result_class, ResultClass.BLOCKED)
        self.assertIn("Unmatched closing marker", res_anon.reason)

    def test_structural_error_mismatched_closing_tag(self):
        text = "<!-- #region blockA -->\ncontent\n<!-- #endregion blockB -->\n"
        res = ManagedRegionParser.merge(text, text)
        self.assertEqual(res.result_class, ResultClass.BLOCKED)
        self.assertIn("Mismatched region end marker", res.reason)

    def test_structural_error_nested_regions(self):
        text = (
            "<!-- #region outer -->\n"
            "<!-- #region inner -->\n"
            "<!-- #endregion inner -->\n"
            "<!-- #endregion outer -->\n"
        )
        res = ManagedRegionParser.merge(text, text)
        self.assertEqual(res.result_class, ResultClass.BLOCKED)
        self.assertIn("Nested region", res.reason)

    def test_concurrent_edit_in_generated_region_is_conflict(self):
        base = "<!-- #region header kind:generated -->\nv1.0\n<!-- #endregion -->\n"
        current = "<!-- #region header kind:generated -->\nv1.0-custom-edit\n<!-- #endregion -->\n"
        new_render = "<!-- #region header kind:generated -->\nv2.0\n<!-- #endregion -->\n"
        res = ManagedRegionParser.merge(current, new_render, base)
        self.assertEqual(res.result_class, ResultClass.CONFLICT)
        self.assertIn("Concurrent edit", res.reason)

    def test_empty_user_region_preserved(self):
        base = "<!-- #region empty kind:user -->\n<!-- #endregion -->\n"
        current = "<!-- #region empty kind:user -->\n<!-- #endregion -->\n"
        new_render = "<!-- #region empty kind:user -->\ndefault template content\n<!-- #endregion -->\n"
        res = ManagedRegionParser.merge(current, new_render, base)
        self.assertEqual(res.result_class, ResultClass.UNCHANGED)
        self.assertEqual(res.merged_text, current)

    def test_newly_added_region_in_template(self):
        current = "<!-- #region existing kind:user -->\nuser content\n<!-- #endregion -->\n"
        new_render = (
            "<!-- #region existing kind:user -->\ndefault content\n<!-- #endregion -->\n"
            "<!-- #region new_feature kind:generated -->\nfeature 1.0\n<!-- #endregion -->\n"
        )
        res = ManagedRegionParser.merge(current, new_render)
        self.assertEqual(res.result_class, ResultClass.REGION_MERGE)
        self.assertIn("user content", res.merged_text)
        self.assertIn("feature 1.0", res.merged_text)


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
            "<!-- #region header kind:generated -->\nv1.0\n<!-- #endregion header -->\n"
            "<!-- user-notes-begin -->\nbase note\n<!-- user-notes-end -->\n"
        )
        curr_text = (
            "<!-- #region header kind:generated -->\nv1.0\n<!-- #endregion header -->\n"
            "<!-- user-notes-begin -->\ncustom user note\n<!-- user-notes-end -->\n"
        )
        new_render = (
            "<!-- #region header kind:generated -->\nv2.0\n<!-- #endregion header -->\n"
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
            "<!-- #region header kind:generated -->\nv2.0-local-hack\n<!-- #endregion header -->\n"
            "<!-- user-notes-begin -->\ncustom user note\n<!-- user-notes-end -->\n"
        )
        target.write_text(conflict_curr, encoding="utf-8")
        v3_render = (
            "<!-- #region header kind:generated -->\nv3.0\n<!-- #endregion header -->\n"
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
