from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..mcp_gateway import EvidenceGateway
from ..trace import TraceWriter


@dataclass
class AgentContext:
    """Case-scoped handoff state shared by coordinator and specialist agents."""

    case: dict[str, Any]
    gateway: EvidenceGateway
    trace: TraceWriter
    findings: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    domain_evidence: dict[str, list[str]] = field(default_factory=dict)

    def register_evidence(self, evidence: dict[str, Any]) -> None:
        evidence_ref = evidence.get("evidence_ref")
        if not isinstance(evidence_ref, str):
            raise ValueError("MCP evidence is missing evidence_ref")
        previous = self.evidence.get(evidence_ref)
        if previous is not None and previous.get("result_hash") != evidence.get("result_hash"):
            raise ValueError(f"evidence_ref reused with a different result: {evidence_ref}")
        self.evidence[evidence_ref] = evidence
        if evidence_ref not in self.evidence_refs:
            self.evidence_refs.append(evidence_ref)

    def register_domain_evidence(self, domain: str, evidence: dict[str, Any]) -> None:
        self.register_evidence(evidence)
        ref = evidence.get("evidence_ref")
        if ref:
            if domain not in self.domain_evidence:
                self.domain_evidence[domain] = []
            if ref not in self.domain_evidence[domain]:
                self.domain_evidence[domain].append(ref)


AgentResult = dict[str, Any]
