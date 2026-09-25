from __future__ import annotations

from .base import AgentContext, AgentResult


async def collect_customer_context(context: AgentContext) -> AgentResult:
    """Fetch customer history only after entity resolution provides its identity."""
    del context
    raise NotImplementedError("Implement customer context investigation")