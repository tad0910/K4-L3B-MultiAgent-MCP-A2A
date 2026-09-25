from __future__ import annotations

from .base import AgentContext, AgentResult


async def resolve_entities(context: AgentContext) -> AgentResult:
    """Resolve claimed/candidate orders and reject unsupported candidates."""
    del context
    raise NotImplementedError("Implement entity resolution and candidate ranking")