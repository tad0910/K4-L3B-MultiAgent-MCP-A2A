from __future__ import annotations

from typing import Any

from .base import AgentContext, AgentResult


class EntityResolverAgent:
    def __init__(self, context: AgentContext) -> None:
        self.context = context
        self.case = context.case
        self.gateway = context.gateway
        self.trace = context.trace
        if not hasattr(context, "cache"):
            context.cache = {}
        self.cache: dict[str, Any] = context.cache

    async def _call_mcp(self, tool_name: str, case_id: str, **kwargs: Any) -> dict[str, Any]:
        cache_key = f"{tool_name}:{sorted(kwargs.items())}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        result = await self.gateway.call(tool_name, case_id=case_id, **kwargs)
        self.cache[cache_key] = result

        evidence_ref = result.get("evidence_ref")
        if evidence_ref:
            if evidence_ref not in self.context.evidence_refs:
                self.context.evidence_refs.append(evidence_ref)
            self.trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="entity-agent",
                tool_name=tool_name,
                evidence_refs=[evidence_ref],
            )
        return result

    async def resolve(self) -> AgentResult:
        case_id = self.case["case_id"]
        candidates = self.case.get("candidate_order_ids", [])

        resolved_order_ids: list[str] = []
        rejected_candidates: list[str] = []

        for candidate in candidates:
            # Lọc nhanh các mã candidate giả dạng 'candidate-...'
            if candidate.startswith("candidate-"):
                rejected_candidates.append(candidate)
                continue

            try:
                await self._call_mcp("get_order", case_id, order_id=candidate)
                resolved_order_ids.append(candidate)
            except Exception:
                rejected_candidates.append(candidate)

        # Đánh giá status và confidence theo kết quả xác thực
        if resolved_order_ids:
            status = "resolved"
            confidence = 0.95
        elif rejected_candidates:
            status = "not_found"
            confidence = 0.1
        else:
            status = "ambiguous"
            confidence = 0.5

        return {
            "status": status,
            "resolved_order_ids": sorted(list(set(resolved_order_ids))),
            "rejected_candidates": sorted(list(set(rejected_candidates))),
            "confidence": confidence,
        }


async def resolve_entities(context: AgentContext) -> AgentResult:
    agent = EntityResolverAgent(context)
    return await agent.resolve()
