# src/student_agent/agents/entity_resolver.py
from __future__ import annotations
from typing import Any
from student_agent.mcp_gateway import EvidenceGateway
from student_agent.trace import TraceWriter

class EntityResolverAgent:
    def __init__(self, gateway: EvidenceGateway, trace: TraceWriter, cache: dict[str, Any]):
        self.gateway = gateway
        self.trace = trace
        self.cache = cache

    async def _call_mcp(self, tool_name: str, case_id: str, **kwargs) -> dict[str, Any]:
        cache_key = f"{tool_name}:{sorted(kwargs.items())}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        result = await self.gateway.call(tool_name, case_id=case_id, **kwargs)
        self.cache[cache_key] = result
        
        # Ghi trace sự kiện tiêu thụ evidence
        self.trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor="entity-agent",
            tool_name=tool_name,
            evidence_refs=[result["evidence_ref"]],
        )
        return result

    async def resolve(self, case: dict[str, Any]) -> dict[str, Any]:
        case_id = case["case_id"]
        candidates = case.get("candidate_order_ids", [])
        
        resolved_order_ids = []
        rejected_candidates = []
        collected_evidence_refs = []
        
        for candidate in candidates:
            # Lọc nhanh mã giả
            if candidate.startswith("candidate-"):
                rejected_candidates.append(candidate)
                continue
                
            try:
                order_evidence = await self._call_mcp("get_order", case_id, order_id=candidate)
                collected_evidence_refs.append(order_evidence["evidence_ref"])
                resolved_order_ids.append(candidate)
            except Exception:
                rejected_candidates.append(candidate)

        # Đánh giá status
        if resolved_order_ids:
            status = "resolved"
            confidence = 0.95
        elif rejected_candidates:
            status = "not_found"
            confidence = 0.1
        else:
            status = "ambiguous"
            confidence = 0.5

        # Lấy Customer Context
        customer_unique_id = case.get("customer_unique_id_hint")
        related_order_ids = set(resolved_order_ids)
        if customer_unique_id:
            try:
                cust_evidence = await self._call_mcp("get_customer_history", case_id, customer_unique_id=customer_unique_id)
                collected_evidence_refs.append(cust_evidence["evidence_ref"])
                for ord_info in cust_evidence.get("data", {}).get("orders", []):
                    if "order_id" in ord_info:
                        related_order_ids.add(ord_info["order_id"])
            except Exception:
                pass

        return {
            "entity_resolution": {
                "status": status,
                "resolved_order_ids": sorted(list(set(resolved_order_ids))),
                "rejected_candidates": sorted(list(set(rejected_candidates))),
                "confidence": confidence,
            },
            "customer_context": {
                "customer_unique_id": customer_unique_id,
                "related_order_ids": sorted(list(related_order_ids)),
            },
            "evidence_refs": collected_evidence_refs,
        }
