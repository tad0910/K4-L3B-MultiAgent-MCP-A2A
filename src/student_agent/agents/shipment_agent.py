from __future__ import annotations

from datetime import datetime
from typing import Any


class ShipmentAgent:
    """Agent responsible for shipment analysis, timeline verification, and delay classification.

    Analyzes shipping timeline events to distinguish between seller dispatch delays
    and logistics transit delays, identifies responsible sellers, and checks timeline completeness.
    """

    def __init__(self, trace_writer: Any | None = None) -> None:
        self.trace_writer = trace_writer

    def parse_datetime(self, dt_val: Any) -> datetime | None:
        if not dt_val or not isinstance(dt_val, str):
            return None
        cleaned = dt_val.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            # Fallback for standard date/time string formats
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(cleaned[:19], fmt)
                except ValueError:
                    continue
        return None

    def analyze(
        self,
        case_id: str,
        order_data: dict[str, Any] | None = None,
        shipment_evidence: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Perform shipping timeline analysis.

        Args:
            case_id: Case ID under investigation.
            order_data: Data payload containing order items, seller information, and timestamps.
            shipment_evidence: Raw MCP evidence block or list of evidence blocks for shipment.

        Returns:
            Dict conforming to l3b-output-v2 shipment_analysis schema:
            {
                "verdict": "on_time" | "seller_delay" | "logistics_delay" | "lost" | "returned" | "conflicting" | "insufficient_evidence",
                "late_seller_ids": list[str],
                "timeline_complete": bool
            }
        """
        order_data = order_data or {}

        # Extract data from evidence if provided
        evidence_data: dict[str, Any] = {}
        evidence_refs: list[str] = []

        if isinstance(shipment_evidence, dict):
            evidence_data = shipment_evidence.get("data", shipment_evidence)
            if "evidence_ref" in shipment_evidence:
                evidence_refs.append(shipment_evidence["evidence_ref"])
        elif isinstance(shipment_evidence, list):
            for item in shipment_evidence:
                if isinstance(item, dict):
                    if "evidence_ref" in item:
                        evidence_refs.append(item["evidence_ref"])
                    data_block = item.get("data", item)
                    if isinstance(data_block, dict):
                        evidence_data.update(data_block)

        # Merge order_data with evidence_data (evidence data takes precedence if present)
        combined = {**order_data, **evidence_data}

        order_status = (combined.get("order_status") or combined.get("status") or "").lower()
        shipment_status = (combined.get("shipment_status") or "").lower()

        # Extract timestamps
        shipping_limit_dt = self.parse_datetime(
            combined.get("shipping_limit_date") or combined.get("seller_dispatch_deadline")
        )
        shipped_dt = self.parse_datetime(
            combined.get("order_delivered_carrier_date")
            or combined.get("shipped_at")
            or combined.get("carrier_pickup_date")
        )
        estimated_delivery_dt = self.parse_datetime(
            combined.get("order_estimated_delivery_date")
            or combined.get("estimated_delivery_date")
        )
        delivered_customer_dt = self.parse_datetime(
            combined.get("order_delivered_customer_date")
            or combined.get("delivered_at")
            or combined.get("delivery_date")
        )

        # Extract seller IDs
        seller_ids: list[str] = []
        raw_sellers = combined.get("seller_ids") or combined.get("sellers") or []
        if isinstance(raw_sellers, list):
            seller_ids = [str(s) for s in raw_sellers if s]
        elif isinstance(raw_sellers, str) and raw_sellers:
            seller_ids = [raw_sellers]

        # Check items for seller info if missing
        items = combined.get("items") or combined.get("order_items") or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and "seller_id" in item:
                    s_id = str(item["seller_id"])
                    if s_id and s_id not in seller_ids:
                        seller_ids.append(s_id)

        late_seller_ids: list[str] = []
        verdict = "insufficient_evidence"

        # Check timeline completeness
        has_essential_dates = bool(
            (shipped_dt or shipping_limit_dt) and (delivered_customer_dt or estimated_delivery_dt)
        )
        timeline_complete = has_essential_dates and bool(delivered_customer_dt or order_status in ("delivered", "canceled", "shipped"))

        # 1. Check for lost / returned status
        if order_status in ("lost", "missing") or shipment_status in ("lost", "missing"):
            verdict = "lost"
        elif order_status in ("returned", "undeliverable") or shipment_status in ("returned", "undeliverable"):
            verdict = "returned"
        elif order_status == "canceled" and not delivered_customer_dt and not shipped_dt:
            # Canceled before dispatch
            if shipping_limit_dt and datetime.now(shipping_limit_dt.tzinfo) > shipping_limit_dt:
                verdict = "seller_delay"
                late_seller_ids = list(set(seller_ids))
            else:
                verdict = "insufficient_evidence"
        else:
            # Evaluate seller delay vs logistics delay vs on time
            is_seller_late = False
            if shipping_limit_dt and shipped_dt:
                if shipped_dt > shipping_limit_dt:
                    is_seller_late = True
            elif shipping_limit_dt and not shipped_dt and delivered_customer_dt:
                # Dispatched date unknown but delivered
                pass

            if is_seller_late:
                verdict = "seller_delay"
                late_seller_ids = list(set(seller_ids))
            elif delivered_customer_dt and estimated_delivery_dt:
                if delivered_customer_dt > estimated_delivery_dt:
                    # Seller was on time (or dispatch unknown), but delivery was past estimated date
                    verdict = "logistics_delay"
                else:
                    verdict = "on_time"
            elif shipped_dt and estimated_delivery_dt:
                # Currently in transit
                now_dt = datetime.now(estimated_delivery_dt.tzinfo)
                if now_dt > estimated_delivery_dt:
                    verdict = "logistics_delay"
                else:
                    verdict = "on_time"
            elif has_essential_dates:
                verdict = "on_time"

        # Special check for conflicting status flag
        if combined.get("is_conflicting") or combined.get("status_conflict"):
            verdict = "conflicting"

        result = {
            "verdict": verdict,
            "late_seller_ids": late_seller_ids,
            "timeline_complete": timeline_complete,
        }

        if self.trace_writer:
            self.trace_writer.emit(
                case_id=case_id,
                event_type="agent_execution",
                actor="shipment-agent",
                decision_code=f"verdict:{verdict}",
                evidence_refs=evidence_refs if evidence_refs else None,
                attributes={"late_sellers": len(late_seller_ids), "timeline_complete": timeline_complete},
            )

        return result


def analyze_shipment(
    case_id: str,
    order_data: dict[str, Any] | None = None,
    shipment_evidence: dict[str, Any] | list[dict[str, Any]] | None = None,
    trace_writer: Any | None = None,
) -> dict[str, Any]:
    """Helper function to execute shipment analysis."""
    agent = ShipmentAgent(trace_writer=trace_writer)
    return agent.analyze(case_id=case_id, order_data=order_data, shipment_evidence=shipment_evidence)
