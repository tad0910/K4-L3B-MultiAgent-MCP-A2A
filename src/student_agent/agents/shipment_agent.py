from __future__ import annotations

from .base import AgentContext, AgentResult


async def analyze_shipment(context: AgentContext) -> AgentResult:
    """Investigate shipment timeline and seller/logistics responsibility."""
    del context
    raise NotImplementedError("Implement shipment investigation")