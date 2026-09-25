from __future__ import annotations

import re
from typing import Any

from .base import AgentContext, AgentResult

HEX_ORDER_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


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
            self.context.register_evidence(result)
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
        claimed_order_id = self.case.get("customer_request", {}).get("claimed_order_id")

        resolved_order_ids: list[str] = []
        rejected_candidates: list[str] = []

        # Sắp xếp candidate: ưu tiên claimed_order_id lên đầu tiên
        sorted_candidates: list[str] = []
        if claimed_order_id and claimed_order_id in candidates:
            sorted_candidates.append(claimed_order_id)
        for c in candidates:
            if c not in sorted_candidates:
                sorted_candidates.append(c)

        for candidate in sorted_candidates:
            # 1. Lọc nhanh không tốn MCP: kiểm tra định dạng hex 32 ký tự
            if not HEX_ORDER_PATTERN.match(candidate):
                rejected_candidates.append(candidate)
                continue

            # 2. Xác thực với MCP get_order
            try:
                order_evidence = await self._call_mcp("get_order", case_id, order_id=candidate)
                data = order_evidence.get("data", {})
                if data and data.get("order_id") == candidate:
                    resolved_order_ids.append(candidate)
                else:
                    rejected_candidates.append(candidate)
            except Exception:
                rejected_candidates.append(candidate)

        # Đánh giá status và confidence theo kết quả
        if resolved_order_ids:
            status = "resolved"
            confidence = 0.98 if claimed_order_id in resolved_order_ids else 0.90
        elif rejected_candidates:
            status = "not_found"
            confidence = 0.10
        else:
            status = "ambiguous"
            confidence = 0.50

        return {
            "status": status,
            "resolved_order_ids": sorted(list(set(resolved_order_ids))),
            "rejected_candidates": sorted(list(set(rejected_candidates))),
            "confidence": confidence,
        }


async def resolve_entities(context: AgentContext) -> AgentResult:
    agent = EntityResolverAgent(context)
    return await agent.resolve()
