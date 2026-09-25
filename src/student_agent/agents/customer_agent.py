from __future__ import annotations

from typing import Any

from .base import AgentContext, AgentResult


async def collect_customer_context(context: AgentContext) -> AgentResult:
    """Investigate customer unique ID and history of related orders."""
    case = context.case
    case_id = case["case_id"]

    if not hasattr(context, "cache"):
        context.cache = {}
    cache: dict[str, Any] = context.cache

    # 1. Xác định customer_unique_id (từ hint hoặc fallback từ order data trong cache)
    customer_unique_id = case.get("customer_unique_id_hint")
    if not customer_unique_id:
        for k, v in cache.items():
            if k.startswith("get_order:"):
                cid = v.get("data", {}).get("customer_id")
                if cid:
                    customer_unique_id = cid
                    break

    # 2. Lấy các order_ids đã được entity_resolver tìm ra
    entity_res = context.findings.get("entity_resolution", {})
    resolved_order_ids = entity_res.get("resolved_order_ids", [])
    related_order_ids: set[str] = set(resolved_order_ids)

    # 3. Truy vấn get_customer_history qua MCP nếu có customer_unique_id
    if customer_unique_id:
        cache_key = (
            f"get_customer_history:[('case_id', '{case_id}'), "
            f"('customer_unique_id', '{customer_unique_id}')]"
        )
        try:
            if cache_key in cache:
                history = cache[cache_key]
            else:
                history = await context.gateway.call(
                    "get_customer_history",
                    case_id=case_id,
                    customer_unique_id=customer_unique_id,
                )
                cache[cache_key] = history

            evidence_ref = history.get("evidence_ref")
            if evidence_ref:
                context.register_evidence(history)
                context.trace.emit(
                    case_id=case_id,
                    event_type="tool_result_consumed",
                    actor="customer-agent",
                    tool_name="get_customer_history",
                    evidence_refs=[evidence_ref],
                )

            orders = history.get("data", {}).get("orders", [])
            for ord_item in orders:
                if isinstance(ord_item, dict) and "order_id" in ord_item:
                    related_order_ids.add(ord_item["order_id"])
        except Exception:
            pass

    # Verifier quy định related_orders phải nằm trong resolved scope
    final_related = [oid for oid in related_order_ids if oid in resolved_order_ids]
    if not final_related and resolved_order_ids:
        final_related = resolved_order_ids

    return {
        "customer_unique_id": customer_unique_id,
        "related_order_ids": sorted(list(set(final_related))),
    }
