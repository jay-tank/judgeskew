"""Data structures shared between the scanner, CLI, and renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

# Severity ordering — a blocker fails the gate; a warning is advisory.
BLOCKER = "blocker"
WARN = "warn"

# Rule identifiers. These are eval-methodology rules, not CWE security rules: a
# biased LLM-as-judge setup produces scores that look rigorous but measure the
# wrong thing — a model grading its own output prefers it, and a judge with no
# rubric or a nonzero temperature scores inconsistently.
JS001 = "JS001"  # same model (or family) both generates AND judges (blocker)
JS002 = "JS002"  # judge call with no rubric/reference, or temperature != 0 (warn)

RULE_SEVERITY = {JS001: BLOCKER, JS002: WARN}


@dataclass
class Finding:
    """A single LLM-as-judge bias issue found in a source file.

    judgeskew never imports or runs the target code — findings are derived
    purely from the parsed AST, so scanning untrusted source is safe."""

    file: str
    line: int
    col: int
    rule: str  # one of JS001 / JS002
    severity: str  # BLOCKER | WARN
    message: str  # what was found
    fix: str  # concrete, actionable hint


@dataclass
class ScanResult:
    """The full result of scanning one or more files."""

    files_scanned: int = 0
    findings: List[Finding] = field(default_factory=list)

    @property
    def blockers(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == BLOCKER]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == WARN]

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)
