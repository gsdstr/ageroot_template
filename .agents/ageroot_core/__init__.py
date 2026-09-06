"""Ageroot Core: Safety-first dry-run comparison and update engine."""

from .comparison import (
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
    ThreeWayComparisonEngine,
    format_summary,
)

__all__ = [
    "AgerootUpdateEngine",
    "ApplyOptions",
    "ApplyOutcome",
    "ComparisonResult",
    "DeterministicNormalizer",
    "DryRunOptions",
    "DryRunReport",
    "ManagedRegionParser",
    "ResultClass",
    "SnapshotStore",
    "Strategy",
    "SummaryReport",
    "ThreeWayComparisonEngine",
    "format_summary",
]
