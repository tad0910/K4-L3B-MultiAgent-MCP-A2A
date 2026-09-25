from __future__ import annotations

from .base import AgentContext, AgentResult


async def analyze_payment(context: AgentContext) -> AgentResult:
    """Reconcile captures, refunds and refundable amounts."""
    del context
    raise NotImplementedError("Implement payment and refund investigation")