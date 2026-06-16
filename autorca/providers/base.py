"""Provider interface and the shared analysis result model."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..log_parser import ErrorDigest


@dataclass
class AnalysisResult:
    """The structured root-cause analysis for one log file.

    Mirrors exactly the report sections the system must produce.
    """
    summary: str = ""                       # one-line headline (the "what", plain)
    simple_explanation: str = ""            # 1-2 sentences, plain non-technical language
    what_happened: str = ""
    why_it_happened: str = ""
    impact: str = ""
    root_cause: str = ""
    resolution_steps: List[str] = field(default_factory=list)
    confidence: str = "Medium"              # High | Medium | Low
    confidence_reason: str = ""

    # classification / identification
    level: str = ""                          # Application | Server | Database | Network
    level_reason: str = ""                   # short why-this-level note
    error_type: str = ""
    exception_class: str = ""
    affected_component: str = ""
    failure_point: str = ""
    probable_trigger: str = ""
    category: str = ""                       # application | infrastructure | integration | configuration
    sequence_of_events: List[str] = field(default_factory=list)
    cascading_failures: List[str] = field(default_factory=list)
    # One explanation per distinct error reason (for multi-endpoint logs).
    # Each item: {reason, title, level, simple_explanation, what_happened, root_cause, fix}
    reason_groups: List[dict] = field(default_factory=list)

    # provenance
    engine: str = ""                         # which provider produced this
    model: str = ""


class AnalysisProvider:
    """Base class for analysis backends."""

    name: str = "base"

    def analyze(self, digest: ErrorDigest, file_name: str) -> AnalysisResult:  # pragma: no cover
        raise NotImplementedError
