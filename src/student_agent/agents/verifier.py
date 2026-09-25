from __future__ import annotations

from .base import AgentContext, AgentResult


async def verify_case(context: AgentContext) -> AgentResult:
    """Apply output invariants and return the final contract-shaped result."""
    del context
    raise NotImplementedError("Implement final output verification")