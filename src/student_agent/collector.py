from __future__ import annotations

import asyncio
from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

TOOL_ALLOWLIST: dict[str, set[str]] = {
    "entity_agent": {"get_customer_history", "get_order"},
    "order_agent": {"get_order", "get_order_items", "get_product_context", "get_sellers"},
    "shipment_agent": {"get_shipment_summary"},
    "payment_agent": {"get_order_payments", "get_payment_timeline", "get_refund_timeline"},
    "policy_agent": {"get_policy"},
}

TOOL_DOMAINS: dict[str, str] = {
    "get_customer_history": "customer",
    "get_order": "order",
    "get_order_items": "item",
    "get_product_context": "product",
    "get_sellers": "seller",
    "get_shipment_summary": "shipment",
    "get_order_payments": "payment",
    "get_payment_timeline": "payment",
    "get_refund_timeline": "payment",
    "get_policy": "policy",
}


class EvidenceCollector:
    """Quản lý thực thi tool, domain-tagged evidence refs, và per-case cache."""

    MAX_CALLS_PER_CASE = 24

    def __init__(self, case_id: str, gateway: EvidenceGateway, trace: TraceWriter) -> None:
        self.case_id = case_id
        self.gateway = gateway
        self.trace = trace
        self._cache: dict[str, dict[str, Any]] = {}
        self._call_count = 0
        self.evidence_refs: list[str] = []
        # Phân loại evidence theo domain để gắn chính xác vào claim_assessments
        self.domain_refs: dict[str, list[str]] = {
            "order": [],
            "customer": [],
            "shipment": [],
            "payment": [],
            "policy": [],
            "item": [],
            "product": [],
            "seller": [],
        }

    def _cache_key(self, tool_name: str, arguments: dict[str, Any]) -> str:
        sorted_args = sorted((k, str(v)) for k, v in arguments.items())
        return f"{tool_name}:{sorted_args}"

    async def call_tool(
        self,
        actor: str,
        tool_name: str,
        **arguments: Any,
    ) -> dict[str, Any]:
        allowed_tools = TOOL_ALLOWLIST.get(actor, set())
        if tool_name not in allowed_tools:
            self.trace.emit(
                case_id=self.case_id,
                event_type="handoff",
                actor=actor,
                decision_code="MCP_REJECTED",
                attributes={"reason": f"Tool {tool_name} not permitted for {actor}"},
            )
            raise PermissionError(f"Actor '{actor}' is not authorized to call tool '{tool_name}'")

        key = self._cache_key(tool_name, arguments)
        if key in self._cache:
            evidence = self._cache[key]
            ref = evidence.get("evidence_ref")
            domain = evidence.get("domain") or TOOL_DOMAINS.get(tool_name, "order")
            if ref and ref not in self.evidence_refs:
                self.evidence_refs.append(ref)
                self.domain_refs.setdefault(domain, []).append(ref)
            self.trace.emit(
                case_id=self.case_id,
                event_type="tool_result_consumed",
                actor=actor,
                tool_name=tool_name,
                evidence_refs=[ref] if ref else [],
                attributes={"cache_hit": True},
            )
            return evidence

        if self._call_count >= self.MAX_CALLS_PER_CASE:
            self.trace.emit(
                case_id=self.case_id,
                event_type="handoff",
                actor=actor,
                decision_code="MCP_EXHAUSTED",
                attributes={"call_count": self._call_count},
            )
            raise RuntimeError(f"MCP call budget exceeded ({self.MAX_CALLS_PER_CASE} calls)")

        self._call_count += 1
        str_args = {k: str(v) for k, v in arguments.items()}
        evidence: dict[str, Any] | None = None
        last_exc: Exception | None = None

        for attempt in (1, 2):
            try:
                evidence = await self.gateway.call(tool_name, case_id=self.case_id, **str_args)
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 1:
                    self.trace.emit(
                        case_id=self.case_id,
                        event_type="handoff",
                        actor=actor,
                        decision_code="MCP_RETRY",
                        attributes={"attempt": attempt, "tool_name": tool_name},
                    )
                    await asyncio.sleep(0.5)
                else:
                    self.trace.emit(
                        case_id=self.case_id,
                        event_type="handoff",
                        actor=actor,
                        decision_code="MCP_EXHAUSTED",
                        attributes={"attempt": attempt, "tool_name": tool_name},
                    )

        if evidence is None:
            raise RuntimeError(f"Failed calling tool {tool_name}: {last_exc}") from last_exc

        self._cache[key] = evidence
        ref = evidence.get("evidence_ref")
        domain = evidence.get("domain") or TOOL_DOMAINS.get(tool_name, "order")
        if ref and ref not in self.evidence_refs:
            self.evidence_refs.append(ref)
            self.domain_refs.setdefault(domain, []).append(ref)

        self.trace.emit(
            case_id=self.case_id,
            event_type="tool_result_consumed",
            actor=actor,
            tool_name=tool_name,
            evidence_refs=[ref] if ref else [],
            attributes={"cache_hit": False},
        )
        return evidence
