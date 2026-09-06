"""Safety-first dry-run comparison and update engine implementation for Ageroot.

This module implements:
1. ResultClass vocabulary & apply eligibility rules.
2. Deterministic normalization (line endings, final newline, trailing whitespace, formatter-only Markdown/YAML noise).
3. Snapshot store with SHA-256 integrity verification.
4. Managed region parser and deterministic region merge engine.
5. Three-way comparison for renderer-owned files (generated & managed-regions).
6. External-link target resolution validation.
7. Deletion escalation and modified-deletion gating.
8. Tiered summary-first confirmation report.
9. Pre-apply workspace drift check.
10. Staged two-phase transactional snapshot commit with rollback.
11. Stdlib-only YAML parser/serializer for config and state.
12. AgerootUpdateEngine unified interface.
"""

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import os
from pathlib import Path
import re
import shutil
from typing import Dict, List, Optional, Set, Tuple, Union


class ResultClass(str, Enum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    UNVERIFIED = "unverified"
    REGION_MERGE = "region-merge"
    CONFLICT = "conflict"
    BLOCKED = "blocked"
    INVALID_LINK = "invalid-link"
    DELETION_PENDING = "deletion-pending"

    @property
    def is_eligible_for_apply(self) -> bool:
        """Only unchanged, changed, unverified, and region-merge are eligible for atomic apply.
        
        Any other class (conflict, blocked, invalid-link, deletion-pending) blocks atomic apply.
        """
        return self in (
            ResultClass.UNCHANGED,
            ResultClass.CHANGED,
            ResultClass.UNVERIFIED,
            ResultClass.REGION_MERGE,
        )


class Strategy(str, Enum):
    GENERATED = "generated"
    MANAGED_REGIONS = "managed-regions"
    EXTERNAL_LINK = "external-link"


@dataclass
class ComparisonResult:
    path: str
    strategy: Strategy
    result_class: ResultClass
    reason: Optional[str] = None
    diff: Optional[str] = None
    proposed_content: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    new_snapshot_content: Optional[str] = None
    new_snapshot_sha256: Optional[str] = None
    excluded: bool = False

    @property
    def is_eligible(self) -> bool:
        return self.result_class.is_eligible_for_apply


@dataclass
class DryRunOptions:
    one_time_unverified_override: bool = False
    allow_deletions: bool = False
    expected_link_targets: Optional[Dict[str, str]] = None


@dataclass
class DryRunReport:
    results: Dict[str, ComparisonResult]
    workspace_hashes: Dict[str, Optional[str]]
    renders: Dict[str, str]
    options: DryRunOptions
    config: dict
    state: dict

    @property
    def is_eligible(self) -> bool:
        """Whether atomic apply can proceed under the current options."""
        for r in self.results.values():
            if r.excluded:
                continue
            if r.result_class == ResultClass.DELETION_PENDING:
                if not self.options.allow_deletions:
                    return False
            elif not r.is_eligible:
                return False
        return True

    @property
    def summary(self) -> str:
        return SummaryReport.format_summary(self)

    def __getitem__(self, path: str) -> ComparisonResult:
        return self.results[path]

    def __iter__(self):
        return iter(self.results.values())

    def get(self, path: str, default=None):
        return self.results.get(path, default)


@dataclass
class ApplyOptions:
    allow_deletions: bool = False


@dataclass
class ApplyOutcome:
    success: bool
    applied_paths: List[str] = field(default_factory=list)
    deleted_paths: List[str] = field(default_factory=list)
    skipped_paths: List[str] = field(default_factory=list)
    error: Optional[str] = None
    staging_dir: Optional[Path] = None


class SimpleYamlHelper:
    """Standard-library-only YAML parser and dumper for Ageroot config and state files."""

    @staticmethod
    def _parse_scalar(val: str):
        val = val.strip()
        if not val:
            return ""
        if val in ("[]", "[ ]"):
            return []
        if val in ("{}", "{ }"):
            return {}
        if val.lower() in ("null", "~"):
            return None
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            return val[1:-1].replace("''", "'")
        if val.lower() == "true":
            return True
        if val.lower() == "false":
            return False
        if val.isdigit():
            return int(val)
        return val

    @classmethod
    def parse(cls, text: str) -> dict:
        lines = []
        for raw_line in text.splitlines():
            # Strip comments outside quotes
            in_quotes = False
            quote_char = None
            comment_idx = -1
            for i, ch in enumerate(raw_line):
                if ch in ('"', "'"):
                    if not in_quotes:
                        in_quotes = True
                        quote_char = ch
                    elif quote_char == ch:
                        in_quotes = False
                elif ch == "#" and not in_quotes:
                    comment_idx = i
                    break
            clean_line = raw_line[:comment_idx] if comment_idx != -1 else raw_line
            if clean_line.strip():
                indent = len(clean_line) - len(clean_line.lstrip())
                lines.append((indent, clean_line.strip()))

        def parse_block(idx: int, current_indent: int):
            if idx >= len(lines):
                return {}, idx

            first_indent, first_line = lines[idx]
            if first_line.startswith("- ") or first_line == "-":
                items = []
                while idx < len(lines):
                    indent, line = lines[idx]
                    if indent < current_indent:
                        break
                    if line.startswith("- ") or line == "-":
                        item_text = line[2:].strip()
                        idx += 1
                        if item_text and ":" in item_text:
                            item_dict = {}
                            k, v = item_text.split(":", 1)
                            k = k.strip()
                            v = v.strip()
                            if v:
                                item_dict[k] = cls._parse_scalar(v)
                            else:
                                val, idx = parse_block(idx, indent + 2)
                                item_dict[k] = val
                            child_indent = indent + 2
                            while idx < len(lines):
                                c_indent, c_line = lines[idx]
                                if c_indent < child_indent or c_line.startswith("-"):
                                    break
                                if ":" in c_line:
                                    ck, cv = c_line.split(":", 1)
                                    ck = ck.strip()
                                    cv = cv.strip()
                                    idx += 1
                                    if cv:
                                        item_dict[ck] = cls._parse_scalar(cv)
                                    else:
                                        cval, idx = parse_block(idx, c_indent + 2)
                                        item_dict[ck] = cval
                                else:
                                    idx += 1
                            items.append(item_dict)
                        elif item_text:
                            items.append(cls._parse_scalar(item_text))
                        else:
                            val, idx = parse_block(idx, indent + 2)
                            items.append(val)
                    else:
                        break
                return items, idx
            else:
                d = {}
                while idx < len(lines):
                    indent, line = lines[idx]
                    if indent < current_indent:
                        break
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k = k.strip()
                        v = v.strip()
                        idx += 1
                        if v:
                            d[k] = cls._parse_scalar(v)
                        else:
                            if idx < len(lines):
                                next_indent, next_line = lines[idx]
                                if next_indent > indent or (next_indent >= indent and next_line.startswith("-")):
                                    val, idx = parse_block(idx, next_indent)
                                    d[k] = val
                                else:
                                    d[k] = None
                            else:
                                d[k] = None
                    else:
                        idx += 1
                return d, idx

        parsed, _ = parse_block(0, 0)
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def dump(cls, data: dict) -> str:
        lines = []

        def _format_scalar(v) -> str:
            if v is None:
                return "null"
            if isinstance(v, bool):
                return str(v).lower()
            if isinstance(v, int):
                return str(v)
            s = str(v)
            if (
                any(c in s for c in [":", "{", "}", "[", "]", ",", "&", "*", "#", "?", "|", "<", ">", "=", "!", "%", "@", "`", "\n"])
                or s == ""
                or s.lower() in ("true", "false", "null", "~")
                or s.isdigit()
            ):
                escaped = s.replace("'", "''")
                return f"'{escaped}'"
            return s

        def _dump_item(val, indent_level=0):
            prefix = "  " * indent_level
            if isinstance(val, dict):
                for k, v in val.items():
                    if isinstance(v, list):
                        if not v:
                            lines.append(f"{prefix}{k}: []")
                        else:
                            lines.append(f"{prefix}{k}:")
                            _dump_item(v, indent_level + 1)
                    elif isinstance(v, dict):
                        if not v:
                            lines.append(f"{prefix}{k}: {{}}")
                        else:
                            lines.append(f"{prefix}{k}:")
                            _dump_item(v, indent_level + 1)
                    elif v is None:
                        lines.append(f"{prefix}{k}: null")
                    elif isinstance(v, bool):
                        lines.append(f"{prefix}{k}: {str(v).lower()}")
                    elif isinstance(v, int):
                        lines.append(f"{prefix}{k}: {v}")
                    else:
                        lines.append(f"{prefix}{k}: {_format_scalar(v)}")
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        if not item:
                            lines.append(f"{prefix}- {{}}")
                            continue
                        first = True
                        for k, v in item.items():
                            if first:
                                if isinstance(v, (dict, list)):
                                    lines.append(f"{prefix}- {k}:")
                                    _dump_item(v, indent_level + 2)
                                else:
                                    lines.append(f"{prefix}- {k}: {_format_scalar(v)}")
                                first = False
                            else:
                                sub_prefix = "  " * (indent_level + 1)
                                if isinstance(v, (dict, list)):
                                    lines.append(f"{sub_prefix}{k}:")
                                    _dump_item(v, indent_level + 2)
                                else:
                                    lines.append(f"{sub_prefix}{k}: {_format_scalar(v)}")
                    else:
                        lines.append(f"{prefix}- {_format_scalar(item)}")

        _dump_item(data)
        return "\n".join(lines) + "\n"


class DeterministicNormalizer:
    """Normalizes content deterministically before comparison.
    
    Removes:
    - CRLF / CR -> LF line endings.
    - Trailing whitespace on each line.
    - Multiple trailing empty lines -> single trailing newline (or none if empty).
    - Formatter-only Markdown noise (consecutive blank lines collapsed to at most 2, trailing spaces removed).
    - Formatter-only YAML noise (trailing spaces, normalize multiple blank lines between documents).
    Preserves exact YAML data/ordering and Markdown structure/text.
    """

    @staticmethod
    def normalize_text(text: str) -> str:
        if text is None:
            return ""
        # 1. Normalize line endings to LF
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        
        # 2. Strip trailing whitespace per line
        lines = [line.rstrip() for line in normalized.split("\n")]
        normalized = "\n".join(lines)
        
        # 3. Collapse 3+ consecutive newlines to at most 2 (standard markdown/yaml formatter normalization)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)

        # 4. Generated-file provenance is metadata, not managed content. A
        # version/commit/timestamp-only change must not create an update.
        normalized = re.sub(
            r"<!-- generated-by: ageroot; template: [^;]+; commit: [^;]+; rendered-at: [^>]+ -->",
            "<!-- generated-by: ageroot -->",
            normalized,
        )

        # 5. Ensure single final newline if non-empty
        normalized = normalized.rstrip()
        if normalized:
            normalized += "\n"
            
        return normalized


class SnapshotStore:
    """Manages project-local baseline snapshots in Git-ignored .agents/snapshots/"""

    def __init__(self, root_dir: Path, snapshot_dir: Optional[Path] = None):
        self.root_dir = Path(root_dir)
        self.snapshot_dir = snapshot_dir or (self.root_dir / ".agents" / "snapshots")

    def get_snapshot_path(self, relative_path: str) -> Path:
        clean = relative_path.removeprefix("./")
        return self.snapshot_dir / clean

    @staticmethod
    def compute_sha256(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def read_snapshot(self, relative_path: str, expected_sha256: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """Reads snapshot file and verifies SHA-256.
        
        Returns:
            (content, error_message)
            If missing: (None, "missing")
            If corrupt: (None, "corrupt")
            If success: (content, None)
        """
        snapshot_file = self.get_snapshot_path(relative_path)
        if not snapshot_file.exists() or not snapshot_file.is_file():
            return None, "missing"

        try:
            content = snapshot_file.read_text(encoding="utf-8")
        except Exception as e:
            return None, f"read_error: {e}"

        if expected_sha256:
            actual_sha = self.compute_sha256(content)
            if actual_sha != expected_sha256:
                return None, "corrupt"

        return content, None

    def write_snapshot_atomic(self, relative_path: str, content: str) -> str:
        """Writes snapshot to disk and returns SHA-256."""
        snapshot_file = self.get_snapshot_path(relative_path)
        snapshot_file.parent.mkdir(parents=True, exist_ok=True)
        snapshot_file.write_text(content, encoding="utf-8")
        return self.compute_sha256(content)


@dataclass
class Region:
    name: str
    kind: str  # 'generated' or 'user'
    content: str  # content inside the markers


class ManagedRegionParser:
    """Parses and merges files containing declared region markers."""

    MARKER_PATTERN = re.compile(
        r"<!--\s*(?:(?P<legacy_start>[\w-]+)-begin|(?P<legacy_end>[\w-]+)-end|region:(?P<named_start>[\w-]+)(?:\s+kind:(?P<kind>generated|user))?|endregion:(?P<named_end>[\w-]+))\s*-->"
    )

    @classmethod
    def parse_structure(cls, text: str) -> Tuple[bool, Optional[List[dict]], Optional[str]]:
        norm_text = DeterministicNormalizer.normalize_text(text)
        lines = norm_text.splitlines(keepends=True)
        
        segments = []
        open_stack = []
        current_segment_lines = []

        line_idx = 0
        while line_idx < len(lines):
            line = lines[line_idx]
            match = cls.MARKER_PATTERN.search(line)
            if match:
                m_dict = match.groupdict()
                start_name = m_dict.get("legacy_start") or m_dict.get("named_start")
                end_name = m_dict.get("legacy_end") or m_dict.get("named_end")

                if start_name:
                    if open_stack:
                        return False, None, f"Nested region '{start_name}' inside '{open_stack[-1]['name']}' is not allowed"
                    # Flush prior unmanaged segment if it has content
                    if current_segment_lines:
                        raw_str = "".join(current_segment_lines)
                        if raw_str:
                            segments.append({
                                "type": "unmanaged",
                                "name": None,
                                "raw": raw_str,
                            })
                        current_segment_lines = []
                    
                    kind = m_dict.get("kind")
                    if not kind:
                        kind = "generated" if start_name in ("generated", "header", "caveman", "rtk") else "user"

                    open_stack.append({
                        "name": start_name,
                        "kind": kind,
                        "start_marker": line,
                    })
                    current_segment_lines.append(line)
                elif end_name:
                    if not open_stack:
                        return False, None, f"Unmatched closing marker for '{end_name}'"
                    top = open_stack.pop()
                    if top["name"] != end_name:
                        return False, None, f"Mismatched region end marker: expected '{top['name']}', got '{end_name}'"
                    
                    current_segment_lines.append(line)
                    segments.append({
                        "type": "region",
                        "name": top["name"],
                        "kind": top["kind"],
                        "start_marker": top["start_marker"],
                        "end_marker": line,
                        "content": "".join(current_segment_lines[1:-1]),
                        "raw": "".join(current_segment_lines),
                    })
                    current_segment_lines = []
            else:
                current_segment_lines.append(line)
            line_idx += 1

        if open_stack:
            return False, None, f"Unclosed region marker for '{open_stack[-1]['name']}'"

        if current_segment_lines:
            raw_str = "".join(current_segment_lines)
            if raw_str:
                segments.append({
                    "type": "unmanaged",
                    "name": None,
                    "raw": raw_str,
                })

        return True, segments, None

    @classmethod
    def merge(
        cls, current_text: str, new_rendered_text: str, baseline_text: Optional[str]
    ) -> Tuple[bool, Optional[str], List[str], Optional[str]]:
        curr_ok, curr_segs, curr_err = cls.parse_structure(current_text)
        if not curr_ok:
            return False, None, [], f"Current file region structure malformed: {curr_err}"

        new_ok, new_segs, new_err = cls.parse_structure(new_rendered_text)
        if not new_ok:
            return False, None, [], f"New render region structure malformed: {new_err}"

        base_segs_map = {}
        if baseline_text:
            base_ok, base_segs, base_err = cls.parse_structure(baseline_text)
            if base_ok:
                for seg in base_segs:
                    if seg.get("type") == "region":
                        base_segs_map[seg["name"]] = seg

        curr_regions = {s["name"]: s for s in curr_segs if s.get("type") == "region"}
        warnings = []

        merged_pieces = []
        for n_seg in new_segs:
            if n_seg["type"] == "unmanaged":
                merged_pieces.append(n_seg["raw"])
            elif n_seg["type"] == "region":
                r_name = n_seg["name"]
                r_kind = n_seg["kind"]
                
                if r_name in curr_regions:
                    c_seg = curr_regions[r_name]
                    if r_kind == "user":
                        # Preserve current user region content
                        merged_pieces.append(c_seg["raw"])
                        if base_segs_map.get(r_name) and DeterministicNormalizer.normalize_text(c_seg["content"]) != DeterministicNormalizer.normalize_text(base_segs_map[r_name]["content"]):
                            warnings.append(f"User region '{r_name}' has local modifications (preserved)")
                    else:  # generated
                        # Check concurrent modification vs baseline
                        if base_segs_map.get(r_name):
                            b_content = DeterministicNormalizer.normalize_text(base_segs_map[r_name]["content"])
                            c_content = DeterministicNormalizer.normalize_text(c_seg["content"])
                            n_content = DeterministicNormalizer.normalize_text(n_seg["content"])
                            if c_content != b_content and c_content != n_content:
                                return False, None, [], f"Concurrent edit in generated region '{r_name}': local modifications conflict with template update"
                        merged_pieces.append(n_seg["raw"])
                else:
                    merged_pieces.append(n_seg["raw"])

        return True, "".join(merged_pieces), warnings, None


class ThreeWayComparisonEngine:
    """Core comparison engine implementing safety-first comparison rules."""

    def __init__(
        self,
        root_dir: Path,
        snapshots_enabled: bool = True,
        snapshot_store: Optional[SnapshotStore] = None,
    ):
        self.root_dir = Path(root_dir)
        self.snapshots_enabled = snapshots_enabled
        self.snapshot_store = snapshot_store or SnapshotStore(self.root_dir)

    def compare_path(
        self,
        relative_path: str,
        strategy: Strategy,
        new_render_content: Optional[str] = None,
        state_entry: Optional[dict] = None,
        one_time_unverified_override: bool = False,
        expected_link_target: Optional[str] = None,
    ) -> ComparisonResult:
        clean_rel = relative_path.removeprefix("./")
        target_file = self.root_dir / clean_rel

        # Planning belongs to the planning-with-files skill. It is never a
        # managed Ageroot path and must not affect comparison or reporting.
        if Path(clean_rel).parts and Path(clean_rel).parts[0] == ".planning":
            return ComparisonResult(
                path=relative_path,
                strategy=strategy,
                result_class=ResultClass.UNCHANGED,
                reason="Excluded planning-with-files state",
                excluded=True,
            )

        # 1. Handle external-link strategy
        if strategy == Strategy.EXTERNAL_LINK:
            if not target_file.exists() and not target_file.is_symlink():
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.INVALID_LINK,
                    reason=f"External link does not exist at {relative_path}",
                )
            
            if expected_link_target:
                try:
                    if target_file.is_symlink():
                        target_resolved = os.readlink(target_file)
                        if expected_link_target not in target_resolved and str(target_file.resolve()) != str(Path(expected_link_target).resolve()):
                            return ComparisonResult(
                                path=relative_path,
                                strategy=strategy,
                                result_class=ResultClass.INVALID_LINK,
                                reason=f"External link points to '{target_resolved}', expected '{expected_link_target}'",
                            )
                except Exception as e:
                    return ComparisonResult(
                        path=relative_path,
                        strategy=strategy,
                        result_class=ResultClass.INVALID_LINK,
                        reason=f"Error validating link target: {e}",
                    )

            return ComparisonResult(
                path=relative_path,
                strategy=strategy,
                result_class=ResultClass.UNCHANGED,
                reason="External link target validated",
            )

        # 2. Handle potential deletion for renderer-owned strategies
        if new_render_content is None:
            if not target_file.exists():
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.UNCHANGED,
                    reason="File absent from render and workspace",
                )
            
            # File exists locally but absent from new render -> potential deletion
            if not self.snapshots_enabled or not state_entry or not state_entry.get("sha256"):
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.DELETION_PENDING,
                    reason="Managed file absent from new render (separate deletion confirmation required)",
                )

            # Check if locally modified vs baseline
            baseline_content, err = self.snapshot_store.read_snapshot(
                relative_path, state_entry.get("sha256")
            )
            if err:
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.BLOCKED,
                    reason=f"Cannot verify deletion safety: snapshot {err}",
                )

            current_content = target_file.read_text(encoding="utf-8")
            if DeterministicNormalizer.normalize_text(current_content) != DeterministicNormalizer.normalize_text(baseline_content):
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.BLOCKED,
                    reason="Locally modified file deletion blocked: file differs from baseline snapshot; preserve or relocate manually",
                )

            return ComparisonResult(
                path=relative_path,
                strategy=strategy,
                result_class=ResultClass.DELETION_PENDING,
                reason="Managed file absent from new render; clean vs baseline (separate deletion confirmation required)",
            )

        # 3. Renderer-owned strategies: GENERATED and MANAGED_REGIONS
        current_exists = target_file.exists()
        current_content = target_file.read_text(encoding="utf-8") if current_exists else ""
        norm_current = DeterministicNormalizer.normalize_text(current_content)
        norm_new = DeterministicNormalizer.normalize_text(new_render_content)

        new_sha = SnapshotStore.compute_sha256(norm_new)

        # Case A: Snapshots disabled OR legacy migration without snapshot OR one-time unverified override
        has_baseline = bool(state_entry and state_entry.get("sha256"))
        
        if not self.snapshots_enabled or not has_baseline or one_time_unverified_override:
            if not current_exists:
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.CHANGED,
                    reason="New file render (untracked previously)",
                    proposed_content=norm_new,
                    new_snapshot_content=norm_new if self.snapshots_enabled else None,
                    new_snapshot_sha256=new_sha if self.snapshots_enabled else None,
                )

            if norm_current == norm_new:
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.UNCHANGED,
                    reason="Identical content (unverified baseline)",
                    proposed_content=norm_new,
                    new_snapshot_content=norm_new if self.snapshots_enabled else None,
                    new_snapshot_sha256=new_sha if self.snapshots_enabled else None,
                )

            reason = "Two-way unverified diff (snapshots disabled)" if not self.snapshots_enabled else (
                "One-time unverified override after escalation" if one_time_unverified_override else "Unverified migration bootstrap (no baseline snapshot)"
            )
            return ComparisonResult(
                path=relative_path,
                strategy=strategy,
                result_class=ResultClass.UNVERIFIED,
                reason=reason,
                proposed_content=norm_new,
                diff=f"--- current\n+++ new_render\n@@ {relative_path} @@",
                new_snapshot_content=norm_new if self.snapshots_enabled else None,
                new_snapshot_sha256=new_sha if self.snapshots_enabled else None,
            )

        # Case B: Snapshots enabled with recorded baseline in state
        baseline_content, snap_err = self.snapshot_store.read_snapshot(
            relative_path, state_entry.get("sha256")
        )
        if snap_err:
            return ComparisonResult(
                path=relative_path,
                strategy=strategy,
                result_class=ResultClass.BLOCKED,
                reason=f"Snapshot integrity failure: {snap_err} (requires cancellation or one-time unverified override)",
            )

        norm_baseline = DeterministicNormalizer.normalize_text(baseline_content)

        # Strategy: GENERATED
        if strategy == Strategy.GENERATED:
            if norm_current == norm_new:
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.UNCHANGED,
                    reason="Content identical to new render",
                    proposed_content=norm_new,
                    new_snapshot_content=norm_new,
                    new_snapshot_sha256=new_sha,
                )
            
            if norm_current == norm_baseline:
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.CHANGED,
                    reason="Clean template update from baseline",
                    proposed_content=norm_new,
                    diff=f"--- baseline\n+++ new_render\n@@ {relative_path} @@",
                    new_snapshot_content=norm_new,
                    new_snapshot_sha256=new_sha,
                )

            if norm_new == norm_baseline:
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.UNCHANGED,
                    reason="Template unchanged; local modifications preserved",
                    warnings=["Local edits present on generated file; template has no updates"],
                    proposed_content=norm_current,
                    new_snapshot_content=norm_baseline,
                    new_snapshot_sha256=state_entry.get("sha256"),
                )

            return ComparisonResult(
                path=relative_path,
                strategy=strategy,
                result_class=ResultClass.CONFLICT,
                reason="Concurrent modification: local edits conflict with template changes on generated file",
                diff=f"--- local\n+++ new_render\n@@ {relative_path} @@",
            )

        # Strategy: MANAGED_REGIONS
        if strategy == Strategy.MANAGED_REGIONS:
            if norm_current == norm_new:
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.UNCHANGED,
                    reason="Content identical to new render",
                    proposed_content=norm_new,
                    new_snapshot_content=norm_new,
                    new_snapshot_sha256=new_sha,
                )

            merge_ok, merged_text, warnings, merge_err = ManagedRegionParser.merge(
                current_text=current_content,
                new_rendered_text=new_render_content,
                baseline_text=baseline_content,
            )

            if not merge_ok:
                if "Concurrent edit" in (merge_err or ""):
                    return ComparisonResult(
                        path=relative_path,
                        strategy=strategy,
                        result_class=ResultClass.CONFLICT,
                        reason=merge_err,
                    )
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.BLOCKED,
                    reason=f"Malformed region structure: {merge_err}",
                )

            norm_merged = DeterministicNormalizer.normalize_text(merged_text)
            if norm_merged == norm_current:
                return ComparisonResult(
                    path=relative_path,
                    strategy=strategy,
                    result_class=ResultClass.UNCHANGED,
                    reason="Proposed region merge resulted in identical content",
                    proposed_content=norm_merged,
                    warnings=warnings,
                    new_snapshot_content=norm_new,
                    new_snapshot_sha256=new_sha,
                )

            return ComparisonResult(
                path=relative_path,
                strategy=strategy,
                result_class=ResultClass.REGION_MERGE,
                reason="Proposed region merge: user regions preserved, generated regions updated",
                proposed_content=norm_merged,
                warnings=warnings,
                diff=f"--- current\n+++ proposed_region_merge\n@@ {relative_path} @@",
                new_snapshot_content=norm_new,
                new_snapshot_sha256=new_sha,
            )

        return ComparisonResult(
            path=relative_path,
            strategy=strategy,
            result_class=ResultClass.BLOCKED,
            reason=f"Unknown strategy '{strategy}'",
        )


class SummaryReport:
    """Generates tiered confirmation summary following ADR-0001."""

    @staticmethod
    def format_summary(
        report_or_results: Union[DryRunReport, List[ComparisonResult], Dict[str, ComparisonResult]]
    ) -> str:
        if isinstance(report_or_results, DryRunReport):
            results_list = list(report_or_results.results.values())
            overall_eligible = report_or_results.is_eligible
        elif isinstance(report_or_results, dict):
            results_list = list(report_or_results.values())
            overall_eligible = all(r.is_eligible for r in results_list if not r.excluded)
        else:
            results_list = list(report_or_results)
            overall_eligible = all(r.is_eligible for r in results_list if not r.excluded)

        results_list = [result for result in results_list if not result.excluded]
        counts: Dict[ResultClass, int] = {rc: 0 for rc in ResultClass}
        for r in results_list:
            counts[r.result_class] += 1

        lines = [
            "# Dry-Run Comparison Summary",
            "",
            "## Overview Counts",
            f"- Changed: {counts[ResultClass.CHANGED]}",
            f"- Unverified: {counts[ResultClass.UNVERIFIED]}",
            f"- Region Merge: {counts[ResultClass.REGION_MERGE]}",
            f"- Unchanged: {counts[ResultClass.UNCHANGED]}",
            f"- Conflicts: {counts[ResultClass.CONFLICT]}",
            f"- Blocked: {counts[ResultClass.BLOCKED]}",
            f"- Invalid Links: {counts[ResultClass.INVALID_LINK]}",
            f"- Deletion Pending: {counts[ResultClass.DELETION_PENDING]}",
            "",
            f"**Atomic Apply Status**: {'ELIGIBLE' if overall_eligible else 'BLOCKED'}",
            "",
        ]

        issues = [
            r for r in results_list
            if not r.is_eligible
            or r.result_class in (ResultClass.UNVERIFIED, ResultClass.DELETION_PENDING)
            or r.warnings
        ]

        if issues:
            lines.append("## Attention Required")
            for r in issues:
                lines.append(f"- **{r.path}** [{r.result_class.value}]")
                if r.reason:
                    lines.append(f"  - Reason: {r.reason}")
                lines.append(f"  - Apply Eligibility: {'Yes' if r.is_eligible else 'No (Blocks update)'}")
                if r.warnings:
                    for w in r.warnings:
                        lines.append(f"  - Warning: {w}")
                if r.diff:
                    lines.append(f"  - Full Diff Access: available on request via `--diff {r.path}`")
            lines.append("")

        return "\n".join(lines)


def format_summary(report: Union[DryRunReport, List[ComparisonResult], Dict[str, ComparisonResult]]) -> str:
    """Format tiered summary-first confirmation report."""
    return SummaryReport.format_summary(report)


class AgerootUpdateEngine:
    """Unified safety-first update engine for Ageroot.
    
    Coordinates configuration/state loading, strategy resolution, three-way comparisons,
    workspace drift checks, and staged two-phase transactional snapshot commits.
    """

    def __init__(
        self,
        root_dir: Path,
        config_path: Optional[Path] = None,
        state_path: Optional[Path] = None,
    ):
        self.root_dir = Path(root_dir).resolve()
        self.config_path = (
            Path(config_path).resolve()
            if config_path
            else (self.root_dir / ".agents" / "ageroot.config.yaml")
        )
        self.state_path = (
            Path(state_path).resolve()
            if state_path
            else (self.root_dir / ".agents" / "ageroot.state.yaml")
        )
        self._load_config_and_state()

    def _load_config_and_state(self) -> None:
        """Loads configuration and state from disk or initializes defaults."""
        if self.config_path.exists() and self.config_path.is_file():
            try:
                self.config = SimpleYamlHelper.parse(self.config_path.read_text(encoding="utf-8"))
            except Exception:
                self.config = {"schema": 1, "snapshots": "enabled"}
        else:
            self.config = {"schema": 1, "snapshots": "enabled"}

        if self.state_path.exists() and self.state_path.is_file():
            try:
                self.state = SimpleYamlHelper.parse(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                self.state = {
                    "schema": 1,
                    "template": {
                        "name": "ageroot",
                        "version": "unknown",
                        "source": "unknown",
                        "commit": "unknown",
                    },
                    "installed_at": None,
                    "rendered_at": None,
                    "managed_files": [],
                }
        else:
            self.state = {
                "schema": 1,
                "template": {
                    "name": "ageroot",
                    "version": "unknown",
                    "source": "unknown",
                    "commit": "unknown",
                },
                "installed_at": None,
                "rendered_at": None,
                "managed_files": [],
            }

        if not isinstance(self.state.get("managed_files"), list):
            self.state["managed_files"] = []

        self.snapshots_enabled = (self.config.get("snapshots", "enabled") != "disabled")
        self.snapshot_store = SnapshotStore(self.root_dir)
        self.comparison_engine = ThreeWayComparisonEngine(
            self.root_dir,
            snapshots_enabled=self.snapshots_enabled,
            snapshot_store=self.snapshot_store,
        )

    @staticmethod
    def _normalize_rel_path(path_str: str) -> str:
        p = Path(path_str).as_posix()
        if p.startswith("./"):
            p = p[2:]
        return p

    def dry_run(
        self,
        renders: dict[str, str],
        options: Optional[DryRunOptions] = None,
    ) -> DryRunReport:
        """Performs strategy-aware safety-first dry-run comparison over new renders."""
        self._load_config_and_state()
        opts = options or DryRunOptions()

        # Build index of existing managed files from state
        state_files_map: Dict[str, dict] = {}
        for entry in self.state.get("managed_files", []):
            if isinstance(entry, dict) and "path" in entry:
                p_norm = self._normalize_rel_path(entry["path"])
                state_files_map[p_norm] = entry
                if p_norm.endswith("/"):
                    state_files_map[p_norm.rstrip("/")] = entry

        results: Dict[str, ComparisonResult] = {}
        processed_state_paths: Set[str] = set()

        # 1. Process all paths provided in renders
        for rel_path, content in renders.items():
            clean_rel = self._normalize_rel_path(rel_path)

            # Exclude .planning paths completely
            if Path(clean_rel).parts and Path(clean_rel).parts[0] == ".planning":
                results[rel_path] = ComparisonResult(
                    path=rel_path,
                    strategy=Strategy.GENERATED,
                    result_class=ResultClass.UNCHANGED,
                    reason="Excluded planning-with-files state",
                    excluded=True,
                )
                continue

            state_entry = state_files_map.get(clean_rel) or state_files_map.get(clean_rel.rstrip("/"))
            if state_entry:
                processed_state_paths.add(self._normalize_rel_path(state_entry.get("path", clean_rel)))
                strategy_str = state_entry.get("strategy", "generated")
                try:
                    strategy = Strategy(strategy_str)
                except ValueError:
                    strategy = Strategy.GENERATED
            else:
                # New path: auto-detect region markers or default to generated
                if ManagedRegionParser.MARKER_PATTERN.search(content):
                    strategy = Strategy.MANAGED_REGIONS
                else:
                    strategy = Strategy.GENERATED

            if strategy == Strategy.EXTERNAL_LINK:
                expected_target = None
                if opts.expected_link_targets:
                    expected_target = opts.expected_link_targets.get(rel_path) or opts.expected_link_targets.get(clean_rel)
                if not expected_target and state_entry:
                    expected_target = state_entry.get("target")

                results[rel_path] = self.comparison_engine.compare_path(
                    rel_path,
                    Strategy.EXTERNAL_LINK,
                    new_render_content=None,
                    state_entry=state_entry,
                    expected_link_target=expected_target,
                )
            else:
                results[rel_path] = self.comparison_engine.compare_path(
                    rel_path,
                    strategy,
                    new_render_content=content,
                    state_entry=state_entry,
                    one_time_unverified_override=opts.one_time_unverified_override,
                )

        # 2. Process managed files in state that were not included in renders
        for entry in self.state.get("managed_files", []):
            if not isinstance(entry, dict) or "path" not in entry:
                continue
            raw_path = entry["path"]
            clean_path = self._normalize_rel_path(raw_path)
            if clean_path in processed_state_paths or clean_path in results or raw_path in results:
                continue

            strategy_str = entry.get("strategy", "generated")
            try:
                strategy = Strategy(strategy_str)
            except ValueError:
                strategy = Strategy.GENERATED

            if strategy == Strategy.EXTERNAL_LINK:
                # State-driven external link validation; never treated as deletion
                expected_target = None
                if opts.expected_link_targets:
                    expected_target = opts.expected_link_targets.get(raw_path) or opts.expected_link_targets.get(clean_path)
                if not expected_target:
                    expected_target = entry.get("target")

                results[raw_path] = self.comparison_engine.compare_path(
                    raw_path,
                    Strategy.EXTERNAL_LINK,
                    new_render_content=None,
                    state_entry=entry,
                    expected_link_target=expected_target,
                )
            else:
                # Renderer-owned path absent from renders -> potential deletion
                results[raw_path] = self.comparison_engine.compare_path(
                    raw_path,
                    strategy,
                    new_render_content=None,
                    state_entry=entry,
                )

        # 3. Collect workspace file hashes for drift verification
        workspace_hashes: Dict[str, Optional[str]] = {}
        for path in results.keys():
            clean = self._normalize_rel_path(path)
            target = self.root_dir / clean
            if target.is_symlink() or (target.exists() and target.is_dir()):
                workspace_hashes[path] = "symlink" if target.is_symlink() else "dir"
            elif target.exists() and target.is_file():
                try:
                    workspace_hashes[path] = hashlib.sha256(target.read_bytes()).hexdigest()
                except Exception:
                    workspace_hashes[path] = None
            else:
                workspace_hashes[path] = None

        return DryRunReport(
            results=results,
            workspace_hashes=workspace_hashes,
            renders=renders,
            options=opts,
            config=self.config,
            state=self.state,
        )

    def apply(
        self,
        report: DryRunReport,
        options: Optional[ApplyOptions] = None,
    ) -> ApplyOutcome:
        """Executes atomic apply with staged two-phase transactional snapshot commit."""
        opts = options or ApplyOptions(allow_deletions=report.options.allow_deletions)

        # 1. Eligibility Check
        for r in report.results.values():
            if r.excluded:
                continue
            if r.result_class == ResultClass.DELETION_PENDING:
                if not opts.allow_deletions:
                    return ApplyOutcome(
                        success=False,
                        error=f"Update blocked: deletion pending for '{r.path}' requires explicit confirmation (allow_deletions=True)",
                    )
            elif not r.is_eligible:
                return ApplyOutcome(
                    success=False,
                    error=f"Update blocked: path '{r.path}' has ineligible status '{r.result_class.value}' ({r.reason})",
                )

        # 2. Workspace Drift Check
        for rel_path, expected_hash in report.workspace_hashes.items():
            clean = self._normalize_rel_path(rel_path)
            target_file = self.root_dir / clean
            if expected_hash is None:
                if target_file.exists():
                    return ApplyOutcome(
                        success=False,
                        error=f"Workspace drift detected for '{rel_path}': file was created after dry-run",
                    )
            elif expected_hash in ("symlink", "dir"):
                if not target_file.exists() and not target_file.is_symlink():
                    return ApplyOutcome(
                        success=False,
                        error=f"Workspace drift detected for '{rel_path}': target missing after dry-run",
                    )
            else:
                if not target_file.exists():
                    return ApplyOutcome(
                        success=False,
                        error=f"Workspace drift detected for '{rel_path}': file was deleted after dry-run",
                    )
                try:
                    actual_hash = hashlib.sha256(target_file.read_bytes()).hexdigest()
                except Exception as e:
                    return ApplyOutcome(
                        success=False,
                        error=f"Workspace drift check failed to read '{rel_path}': {e}",
                    )
                if actual_hash != expected_hash:
                    return ApplyOutcome(
                        success=False,
                        error=f"Workspace drift detected for '{rel_path}': file modified after dry-run (hash mismatch)",
                    )

        # 3. Two-Phase Transactional Staged Commit
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        staging_dir = self.root_dir / ".agents" / f".staging-{timestamp_str}"
        stage_workspace = staging_dir / "workspace"
        stage_snapshots = staging_dir / "snapshots"
        stage_backup = staging_dir / "backup"

        try:
            stage_workspace.mkdir(parents=True, exist_ok=True)
            stage_snapshots.mkdir(parents=True, exist_ok=True)

            applied_paths: List[str] = []
            deleted_paths: List[str] = []
            skipped_paths: List[str] = []

            # Phase 1: Stage and verify SHA-256
            for r in report.results.values():
                if r.excluded or r.result_class == ResultClass.UNCHANGED:
                    skipped_paths.append(r.path)
                    continue

                if r.result_class in (ResultClass.CHANGED, ResultClass.UNVERIFIED, ResultClass.REGION_MERGE):
                    clean_rel = self._normalize_rel_path(r.path)
                    staged_ws_file = stage_workspace / clean_rel
                    staged_ws_file.parent.mkdir(parents=True, exist_ok=True)
                    content = r.proposed_content or ""
                    staged_ws_file.write_text(content, encoding="utf-8")

                    ws_sha = hashlib.sha256(staged_ws_file.read_bytes()).hexdigest()
                    expected_ws_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    if ws_sha != expected_ws_sha:
                        raise RuntimeError(f"Staged file SHA-256 verification failed for '{r.path}'")

                    if self.snapshots_enabled and r.new_snapshot_content is not None:
                        staged_snap_file = stage_snapshots / clean_rel
                        staged_snap_file.parent.mkdir(parents=True, exist_ok=True)
                        snap_content = r.new_snapshot_content
                        staged_snap_file.write_text(snap_content, encoding="utf-8")
                        snap_sha = hashlib.sha256(staged_snap_file.read_bytes()).hexdigest()
                        expected_snap_sha = r.new_snapshot_sha256 or hashlib.sha256(snap_content.encode("utf-8")).hexdigest()
                        if snap_sha != expected_snap_sha:
                            raise RuntimeError(f"Staged snapshot SHA-256 verification failed for '{r.path}'")

                    applied_paths.append(r.path)

                elif r.result_class == ResultClass.DELETION_PENDING and opts.allow_deletions:
                    deleted_paths.append(r.path)

            # Construct updated state dictionary
            now = datetime.now(timezone.utc)
            new_state = copy.deepcopy(self.state)
            new_state["rendered_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")

            existing_managed_map: Dict[str, dict] = {
                self._normalize_rel_path(item["path"]): item
                for item in self.state.get("managed_files", [])
                if isinstance(item, dict) and "path" in item
            }

            deleted_clean_set = {self._normalize_rel_path(p) for p in deleted_paths}
            updated_managed_files = []

            for r in report.results.values():
                if r.excluded:
                    continue
                clean_rel = self._normalize_rel_path(r.path)
                if clean_rel in deleted_clean_set:
                    continue

                entry: dict = {"path": r.path, "strategy": r.strategy.value}
                if r.strategy != Strategy.EXTERNAL_LINK:
                    entry["snapshot"] = f".agents/snapshots/{clean_rel}"
                    if r.new_snapshot_sha256:
                        entry["sha256"] = r.new_snapshot_sha256
                    elif clean_rel in existing_managed_map and "sha256" in existing_managed_map[clean_rel]:
                        entry["sha256"] = existing_managed_map[clean_rel]["sha256"]
                else:
                    if clean_rel in existing_managed_map and "target" in existing_managed_map[clean_rel]:
                        entry["target"] = existing_managed_map[clean_rel]["target"]

                updated_managed_files.append(entry)

            # Retain any prior managed files that were uninspected and not deleted
            seen_clean = {self._normalize_rel_path(e["path"]) for e in updated_managed_files}
            for clean_p, old_entry in existing_managed_map.items():
                if clean_p not in seen_clean and clean_p not in deleted_clean_set:
                    updated_managed_files.append(old_entry)

            new_state["managed_files"] = updated_managed_files

            # Stage state file
            staged_state_file = staging_dir / "ageroot.state.yaml"
            staged_state_content = SimpleYamlHelper.dump(new_state)
            staged_state_file.write_text(staged_state_content, encoding="utf-8")
            state_sha = hashlib.sha256(staged_state_file.read_bytes()).hexdigest()
            expected_state_sha = hashlib.sha256(staged_state_content.encode("utf-8")).hexdigest()
            if state_sha != expected_state_sha:
                raise RuntimeError("Staged state file SHA-256 verification failed")

            # Phase 2: Transactional commit with backup and atomic moves
            stage_backup.mkdir(parents=True, exist_ok=True)
            # Tuple: (destination_path, backup_path_or_None, existed_previously)
            moved_records: List[Tuple[Path, Optional[Path], bool]] = []

            try:
                # 1. Atomically move workspace files
                for rel_path in applied_paths:
                    clean_rel = self._normalize_rel_path(rel_path)
                    src_file = stage_workspace / clean_rel
                    dst_file = self.root_dir / clean_rel
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    if dst_file.exists():
                        backup_file = stage_backup / "ws" / clean_rel
                        backup_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(dst_file, backup_file)
                        moved_records.append((dst_file, backup_file, True))
                    else:
                        moved_records.append((dst_file, None, False))
                    src_file.replace(dst_file)

                # 2. Atomically move baseline snapshots
                if self.snapshots_enabled:
                    for rel_path in applied_paths:
                        clean_rel = self._normalize_rel_path(rel_path)
                        src_snap = stage_snapshots / clean_rel
                        if src_snap.exists():
                            dst_snap = self.snapshot_store.get_snapshot_path(clean_rel)
                            dst_snap.parent.mkdir(parents=True, exist_ok=True)
                            if dst_snap.exists():
                                backup_snap = stage_backup / "snap" / clean_rel
                                backup_snap.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(dst_snap, backup_snap)
                                moved_records.append((dst_snap, backup_snap, True))
                            else:
                                moved_records.append((dst_snap, None, False))
                            src_snap.replace(dst_snap)

                # 3. Handle deletions
                for rel_path in deleted_paths:
                    clean_rel = self._normalize_rel_path(rel_path)
                    dst_file = self.root_dir / clean_rel
                    if dst_file.exists():
                        backup_file = stage_backup / "del_ws" / clean_rel
                        backup_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(dst_file, backup_file)
                        moved_records.append((dst_file, backup_file, True))
                        dst_file.unlink()

                    dst_snap = self.snapshot_store.get_snapshot_path(clean_rel)
                    if dst_snap.exists():
                        backup_snap = stage_backup / "del_snap" / clean_rel
                        backup_snap.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(dst_snap, backup_snap)
                        moved_records.append((dst_snap, backup_snap, True))
                        dst_snap.unlink()

                # 4. Atomic state update
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                if self.state_path.exists():
                    backup_state = stage_backup / "ageroot.state.yaml"
                    shutil.copy2(self.state_path, backup_state)
                    moved_records.append((self.state_path, backup_state, True))
                else:
                    moved_records.append((self.state_path, None, False))
                staged_state_file.replace(self.state_path)

                # Update in-memory state
                self.state = new_state

            except Exception as move_err:
                # Rollback on move failure
                for target, backup, existed in reversed(moved_records):
                    try:
                        if existed and backup and backup.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(backup, target)
                        elif not existed and target.exists():
                            target.unlink()
                    except Exception:
                        pass
                raise move_err

            # Cleanup staging directory after successful apply
            shutil.rmtree(staging_dir, ignore_errors=True)

            return ApplyOutcome(
                success=True,
                applied_paths=applied_paths,
                deleted_paths=deleted_paths,
                skipped_paths=skipped_paths,
            )

        except Exception as e:
            # Staging or commit error: clean up and rollback
            shutil.rmtree(staging_dir, ignore_errors=True)
            return ApplyOutcome(
                success=False,
                error=f"Apply failed: {e}",
                staging_dir=staging_dir,
            )

    def format_summary(self, report: DryRunReport) -> str:
        """Formats tiered summary-first confirmation report."""
        return SummaryReport.format_summary(report)
