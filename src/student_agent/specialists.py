from __future__ import annotations

from typing import Any
from .collector import EvidenceCollector


class EntityAgent:
    """Xác định và phân giải tập order_ids từ gợi ý candidate và customer_unique_id."""

    def __init__(self, collector: EvidenceCollector) -> None:
        self.collector = collector
        self.actor = "entity_agent"

    async def resolve(self, case: dict[str, Any]) -> dict[str, Any]:
        customer_request = case.get("customer_request", {})
        customer_unique_id = (
            case.get("customer_unique_id")
            or case.get("customer_unique_id_hint")
            or customer_request.get("customer_unique_id")
        )
        candidate_order_ids = case.get("candidate_order_ids") or []
        claimed_order_id = customer_request.get("claimed_order_id") or case.get("order_id")

        resolved_orders: set[str] = set()
        rejected_candidates: set[str] = set()
        related_orders: set[str] = set()

        if customer_unique_id:
            try:
                ev = await self.collector.call_tool(
                    self.actor,
                    "get_customer_history",
                    customer_unique_id=customer_unique_id,
                )
                data = ev.get("data", {})
                orders = data.get("orders") if isinstance(data, dict) else (data if isinstance(data, list) else [])
                for ord_entry in orders:
                    oid = ord_entry.get("order_id") if isinstance(ord_entry, dict) else str(ord_entry)
                    if oid:
                        related_orders.add(oid)
            except Exception:
                pass

        for cid in candidate_order_ids:
            if cid.startswith("candidate-"):
                rejected_candidates.add(cid)
            elif claimed_order_id and cid == claimed_order_id:
                resolved_orders.add(cid)
            elif cid in related_orders:
                resolved_orders.add(cid)
            else:
                rejected_candidates.add(cid)

        if not resolved_orders and claimed_order_id:
            resolved_orders.add(claimed_order_id)

        if claimed_order_id:
            related_orders.add(claimed_order_id)

        status = "resolved" if resolved_orders else "not_found"
        confidence = 0.98 if status == "resolved" else 0.2

        return {
            "entity_resolution": {
                "status": status,
                "resolved_order_ids": sorted(list(resolved_orders)),
                "rejected_candidates": sorted(list(rejected_candidates)),
                "confidence": confidence,
            },
            "customer_context": {
                "customer_unique_id": customer_unique_id,
                "related_order_ids": sorted(list(related_orders)),
            },
        }


class OrderAgent:
    """Thu thập thông tin chi tiết đơn hàng, sản phẩm, sellers và product context."""

    def __init__(self, collector: EvidenceCollector) -> None:
        self.collector = collector
        self.actor = "order_agent"

    async def investigate(
        self, resolved_order_ids: list[str], *, need_items: bool = True
    ) -> dict[str, Any]:
        item_ids: set[str] = set()
        seller_ids: set[str] = set()
        orders_data: list[dict[str, Any]] = []
        total_val = 0.0

        for order_id in resolved_order_ids:
            # 1. get_order
            try:
                ev_order = await self.collector.call_tool(self.actor, "get_order", order_id=order_id)
                data = ev_order.get("data", {})
                if isinstance(data, dict):
                    orders_data.append(data)
            except Exception:
                pass

            # 2. get_order_items (chỉ gọi khi cần item/seller)
            if need_items:
                try:
                    ev_items = await self.collector.call_tool(self.actor, "get_order_items", order_id=order_id)
                    data = ev_items.get("data", [])
                    items_list = data if isinstance(data, list) else data.get("items", [])
                    for item in items_list:
                        if isinstance(item, dict):
                            iid = item.get("order_item_id") or item.get("product_id")
                            sid = item.get("seller_id")
                            p = float(item.get("price", 0.0))
                            f = float(item.get("freight_value", 0.0))
                            total_val += (p + f)
                            if iid:
                                item_ids.add(str(iid))
                            if sid:
                                seller_ids.add(str(sid))
                except Exception:
                    pass

        return {
            "order_ids": sorted(resolved_order_ids),
            "item_ids": sorted(list(item_ids)),
            "seller_ids": sorted(list(seller_ids)),
            "orders_detail": orders_data,
            "order_total_brl": round(total_val, 2) if total_val > 0 else None,
        }


class ShipmentAgent:
    """Phân tích timeline giao hàng và phân định trách nhiệm seller vs logistics."""

    def __init__(self, collector: EvidenceCollector) -> None:
        self.collector = collector
        self.actor = "shipment_agent"

    async def investigate(
        self, resolved_order_ids: list[str], *, default_verdict: str = "on_time"
    ) -> dict[str, Any]:
        shipment_ids: set[str] = set()
        late_sellers: set[str] = set()
        verdicts: list[str] = []
        is_timeline_complete = True

        for order_id in resolved_order_ids:
            try:
                ev_shipment = await self.collector.call_tool(
                    self.actor, "get_shipment_summary", order_id=order_id
                )
                data = ev_shipment.get("data", {})
                sid = data.get("shipment_id") or data.get("tracking_id") or f"shipment-{order_id[:12]}"
                if sid:
                    shipment_ids.add(str(sid))

                # Thu thập seller chậm giao nếu có
                shipping_limits = data.get("shipping_limits", [])
                for sl in shipping_limits:
                    if isinstance(sl, dict) and sl.get("seller_id"):
                        late_sellers.add(str(sl["seller_id"]))

                events = data.get("events", [])
                for ev in events:
                    if isinstance(ev, dict):
                        actor = ev.get("actor")
                        etype = ev.get("event_type")
                        if actor == "logistics_provider" or "carrier" in str(etype):
                            verdicts.append("logistics_delay")
                        elif actor == "seller" or "seller" in str(etype):
                            verdicts.append("seller_delay")

                v = data.get("verdict")
                if v:
                    verdicts.append(v)
                elif not events:
                    verdicts.append("on_time")

                if data.get("timeline_complete") is False:
                    is_timeline_complete = False
            except Exception:
                is_timeline_complete = False

        if "lost" in verdicts:
            final_verdict = "lost"
        elif "returned" in verdicts:
            final_verdict = "returned"
        elif "seller_delay" in verdicts:
            final_verdict = "seller_delay"
        elif "logistics_delay" in verdicts:
            final_verdict = "logistics_delay"
        elif all(v == "on_time" for v in verdicts) and verdicts:
            final_verdict = "on_time"
        else:
            final_verdict = default_verdict

        # Chỉ giữ late_seller_ids nếu là seller_delay
        final_late_sellers = sorted(list(late_sellers)) if final_verdict == "seller_delay" else []

        return {
            "shipment_analysis": {
                "verdict": final_verdict,
                "late_seller_ids": final_late_sellers,
                "timeline_complete": is_timeline_complete,
            },
            "shipment_ids": sorted(list(shipment_ids)),
        }


class PaymentAgent:
    """Đối soát thanh toán, capture và timeline."""

    def __init__(self, collector: EvidenceCollector) -> None:
        self.collector = collector
        self.actor = "payment_agent"

    async def investigate(
        self, resolved_order_ids: list[str], *, need_timeline: bool = True
    ) -> dict[str, Any]:
        payment_refs: set[str] = set()
        captured_total = 0.0
        refunded_total = 0.0
        has_payment_data = False
        duplicate_capture = False
        capture_mismatch = False
        refund_pending = False
        refund_failed = False

        for order_id in resolved_order_ids:
            # 1. get_order_payments
            try:
                ev_pay = await self.collector.call_tool(self.actor, "get_order_payments", order_id=order_id)
                data = ev_pay.get("data", [])
                payments = data if isinstance(data, list) else data.get("payments", [])
                if payments:
                    has_payment_data = True
                    for idx, p in enumerate(payments):
                        pref = p.get("payment_reference") or p.get("payment_id") or f"pay-{order_id[:8]}-{idx}"
                        if pref:
                            payment_refs.add(str(pref))
                        val = float(p.get("payment_value", 0.0))
                        captured_total += val
            except Exception:
                pass

            # 2. get_payment_timeline (chỉ gọi khi cần timeline phân tích)
            if need_timeline:
                try:
                    ev_pt = await self.collector.call_tool(self.actor, "get_payment_timeline", order_id=order_id)
                    pdata = ev_pt.get("data", {})
                    events = pdata.get("events", []) if isinstance(pdata, dict) else []
                    for ev in events:
                        if isinstance(ev, dict):
                            etype = ev.get("event_type")
                            if etype == "refund_failed":
                                refund_failed = True
                            elif etype == "refund_pending":
                                refund_pending = True
                            elif etype == "duplicate_charge":
                                duplicate_capture = True
                            elif etype == "refunded":
                                refunded_total += float(ev.get("amount_brl", 0.0))
                except Exception:
                    pass

        if not has_payment_data:
            verdict = "insufficient_evidence"
            final_captured = None
            final_refunded = None
            final_refundable = None
        else:
            final_captured = round(captured_total, 2)
            final_refunded = round(refunded_total, 2)
            final_refundable = max(0.0, round(final_captured - final_refunded, 2))

            if refund_failed:
                verdict = "refund_failed"
            elif refund_pending:
                verdict = "refund_pending"
            elif duplicate_capture:
                verdict = "duplicate_capture"
            elif capture_mismatch:
                verdict = "capture_mismatch"
            elif final_refunded >= final_captured and final_captured > 0:
                verdict = "refunded"
            else:
                verdict = "reconciled"

        return {
            "payment_analysis": {
                "verdict": verdict,
                "captured_total_brl": final_captured,
                "refunded_total_brl": final_refunded,
                "refundable_total_brl": final_refundable,
            },
            "payment_references": sorted(list(payment_refs)),
        }
