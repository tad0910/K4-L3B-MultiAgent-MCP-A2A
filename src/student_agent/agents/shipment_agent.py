from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import AgentContext, AgentResult


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        # Xử lý ISO format có timezone
        return datetime.fromisoformat(val)
    except Exception:
        return None


async def analyze_shipment(context: AgentContext) -> AgentResult:
    """Investigate shipment timeline and seller/logistics responsibility."""
    case = context.case
    case_id = case["case_id"]

    if not hasattr(context, "cache"):
        context.cache = {}
    cache: dict[str, Any] = context.cache

    # Lấy resolved_order_ids từ bước entity resolution
    entity_res = context.findings.get("entity_resolution", {})
    resolved_order_ids = entity_res.get("resolved_order_ids", [])

    if not resolved_order_ids:
        return {
            "verdict": "insufficient_evidence",
            "late_seller_ids": [],
            "timeline_complete": False,
        }

    order_id = resolved_order_ids[0]
    cache_key = f"get_shipment_summary:[('case_id', '{case_id}'), ('order_id', '{order_id}')]"

    try:
        if cache_key in cache:
            evidence = cache[cache_key]
        else:
            evidence = await context.gateway.call(
                "get_shipment_summary",
                case_id=case_id,
                order_id=order_id,
            )
            cache[cache_key] = evidence

        evidence_ref = evidence.get("evidence_ref")
        if evidence_ref:
            context.register_domain_evidence("shipment", evidence)
            context.trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="shipment-agent",
                tool_name="get_shipment_summary",
                evidence_refs=[evidence_ref],
            )

        data = evidence.get("data", {})
        order_status = data.get("order_status", "")
        delivered_carrier_at = _parse_dt(data.get("delivered_carrier_at"))
        delivered_customer_at = _parse_dt(data.get("delivered_customer_at"))
        estimated_delivery_at = _parse_dt(data.get("estimated_delivery_at"))

        shipping_limits = data.get("shipping_limits", [])
        events = data.get("events", [])

        # 1. Xác định late_seller_ids
        late_sellers: set[str] = set()
        for limit in shipping_limits:
            seller_id = limit.get("seller_id")
            limit_dt = _parse_dt(limit.get("shipping_limit_at"))
            if seller_id and limit_dt and delivered_carrier_at and delivered_carrier_at > limit_dt:
                late_sellers.add(seller_id)

        # 2. Xác định timeline_complete
        timeline_complete = bool(
            delivered_carrier_at is not None
            and delivered_customer_at is not None
            and order_status == "delivered"
        )

        # 3. Xác định verdict
        # Kiểm tra events trước
        has_logistics_event = any(
            e.get("actor") == "logistics_provider"
            and e.get("event_type") in ("delivered_late", "carrier_delay")
            for e in events
        )
        has_seller_delay_event = any(
            e.get("actor") == "seller"
            and e.get("event_type") in ("delayed_dispatch", "seller_delay")
            for e in events
        )

        if order_status in ("canceled", "unavailable"):
            verdict = "returned" if delivered_customer_at else "on_time"
        elif has_seller_delay_event or (
            late_sellers
            and delivered_customer_at
            and estimated_delivery_at
            and delivered_customer_at > estimated_delivery_at
        ):
            verdict = "seller_delay"
        elif has_logistics_event or (
            delivered_customer_at
            and estimated_delivery_at
            and delivered_customer_at > estimated_delivery_at
        ):
            verdict = "logistics_delay"
        elif (
            delivered_customer_at
            and estimated_delivery_at
            and delivered_customer_at <= estimated_delivery_at
            or order_status == "delivered"
        ):
            verdict = "on_time"
        else:
            verdict = "insufficient_evidence"

        return {
            "verdict": verdict,
            "late_seller_ids": sorted(list(late_sellers)),
            "timeline_complete": timeline_complete,
        }

    except Exception:
        return {
            "verdict": "insufficient_evidence",
            "late_seller_ids": [],
            "timeline_complete": False,
        }
