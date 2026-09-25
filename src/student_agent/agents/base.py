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


AgentResult = dict[str, Any]
