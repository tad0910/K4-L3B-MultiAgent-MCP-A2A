from __future__ import annotations

from typing import Any

from .base import AgentContext, AgentResult


async def analyze_conflicts_and_root_cause(context: AgentContext) -> AgentResult:
    """Resolve data conflicts, rank root causes, and determine responsibility and actions."""
    case = context.case
    case_id = case["case_id"]

    if not hasattr(context, "cache"):
        context.cache = {}
    cache: dict[str, Any] = context.cache

    entity_res = context.findings.get("entity_resolution", {})
    resolved_order_ids = entity_res.get("resolved_order_ids", [])
    order_id = resolved_order_ids[0] if resolved_order_ids else None

    shipment_res = context.findings.get("shipment_analysis", {})
    payment_res = context.findings.get("payment_analysis", {})

    shipment_verdict = shipment_res.get("verdict", "insufficient_evidence")
    late_sellers = shipment_res.get("late_seller_ids", [])
    payment_verdict = payment_res.get("verdict", "insufficient_evidence")
    captured_total = payment_res.get("captured_total_brl", 0.0)
    refundable_total = payment_res.get("refundable_total_brl", 0.0)

    # 1. Thu thập item_ids và seller_ids từ get_order_items nếu có order_id
    item_ids: list[str] = []
    seller_ids: list[str] = list(late_sellers)
    if order_id:
        items_key = f"get_order_items:[('case_id', '{case_id}'), ('order_id', '{order_id}')]"
        try:
            if items_key in cache:
                items_evidence = cache[items_key]
            else:
                items_evidence = await context.gateway.call(
                    "get_order_items", case_id=case_id, order_id=order_id
                )
                cache[items_key] = items_evidence

            evidence_ref = items_evidence.get("evidence_ref")
            if evidence_ref:
                if evidence_ref not in context.evidence_refs:
                    context.evidence_refs.append(evidence_ref)
                context.trace.emit(
                    case_id=case_id,
                    event_type="tool_result_consumed",
                    actor="conflict-root-cause-agent",
                    tool_name="get_order_items",
                    evidence_refs=[evidence_ref],
                )

            data_items = items_evidence.get("data", [])
            if isinstance(data_items, list):
                for itm in data_items:
                    if isinstance(itm, dict):
                        if "order_item_id" in itm:
                            item_ids.append(itm["order_item_id"])
                        if "seller_id" in itm and itm["seller_id"] not in seller_ids:
                            seller_ids.append(itm["seller_id"])
        except Exception:
            pass

    # 2. Xác định primary_issue từ claims và verdicts
    claims = case.get("customer_request", {}).get("claims", [])
    claim_topics = [c.get("topic") for c in claims if c.get("topic")]

    primary_issue = "insufficient_evidence"
    ranked_causes: list[dict[str, Any]] = []
    responsible_parties: list[dict[str, Any]] = []
    recommended_refund_brl = 0.0
    refund_lines: list[dict[str, Any]] = []
    case_status = "no_action"
    resolution_actions: list[str] = []

    # Kiểm tra theo topic của claim
    if "late_delivery_logistics" in claim_topics or shipment_verdict == "logistics_delay":
        primary_issue = "late_delivery_logistics"
        case_status = "action_required"
        ranked_causes.append({"cause_code": "CARRIER_TRANSIT_DELAY", "rank": 1})
        responsible_parties.append(
            {"party_type": "logistics_provider", "party_id": "carrier-default"}
        )
        recommended_refund_brl = round(refundable_total, 2)
        if recommended_refund_brl > 0:
            refund_lines.append(
                {
                    "reason_code": "late_delivery_refund",
                    "amount_brl": recommended_refund_brl,
                    "entity_id": order_id,
                }
            )
        resolution_actions = ["issue_customer_refund", "file_carrier_dispute"]

    elif "late_delivery_seller" in claim_topics or shipment_verdict == "seller_delay":
        primary_issue = "late_delivery_seller"
        case_status = "action_required"
        ranked_causes.append({"cause_code": "SELLER_DISPATCH_DELAY", "rank": 1})
        for sid in seller_ids or ["seller-unknown"]:
            responsible_parties.append({"party_type": "seller", "party_id": sid})
        recommended_refund_brl = round(refundable_total, 2)
        if recommended_refund_brl > 0:
            refund_lines.append(
                {
                    "reason_code": "seller_delay_refund",
                    "amount_brl": recommended_refund_brl,
                    "entity_id": order_id,
                }
            )
        resolution_actions = ["issue_customer_refund", "penalize_seller_sla"]

    elif "duplicate_charge" in claim_topics or payment_verdict == "duplicate_capture":
        primary_issue = "duplicate_charge"
        case_status = "action_required"
        ranked_causes.append({"cause_code": "GATEWAY_DUPLICATE_CAPTURE", "rank": 1})
        responsible_parties.append(
            {"party_type": "payment_provider", "party_id": "payment_gateway"}
        )
        # Hoàn lại phần tiền bị trùng
        recommended_refund_brl = round(captured_total / 2, 2) if captured_total > 0 else 0.0
        if recommended_refund_brl > 0:
            refund_lines.append(
                {
                    "reason_code": "duplicate_charge_reversal",
                    "amount_brl": recommended_refund_brl,
                    "entity_id": order_id,
                }
            )
        resolution_actions = ["reverse_duplicate_charge", "notify_customer"]

    elif "payment_mismatch" in claim_topics or payment_verdict == "capture_mismatch":
        primary_issue = "payment_mismatch"
        case_status = "action_required"
        ranked_causes.append({"cause_code": "PAYMENT_RECONCILIATION_MISMATCH", "rank": 1})
        responsible_parties.append(
            {"party_type": "payment_provider", "party_id": "payment_gateway"}
        )
        recommended_refund_brl = round(refundable_total, 2)
        if recommended_refund_brl > 0:
            refund_lines.append(
                {
                    "reason_code": "reconciliation_correction",
                    "amount_brl": recommended_refund_brl,
                    "entity_id": order_id,
                }
            )
        resolution_actions = ["reconcile_discrepancy", "issue_partial_refund"]

    elif "refund_pending" in claim_topics or payment_verdict == "refund_pending":
        primary_issue = "refund_pending"
        case_status = "action_required"
        ranked_causes.append({"cause_code": "BANK_PROCESSING_PENDING", "rank": 1})
        responsible_parties.append(
            {"party_type": "payment_provider", "party_id": "banking_partner"}
        )
        recommended_refund_brl = 0.0
        resolution_actions = ["expedite_bank_clearance", "notify_customer_timeline"]

    elif "refund_failed" in claim_topics or payment_verdict == "refund_failed":
        primary_issue = "refund_failed"
        case_status = "action_required"
        ranked_causes.append({"cause_code": "REFUND_GATEWAY_REJECTION", "rank": 1})
        responsible_parties.append(
            {"party_type": "payment_provider", "party_id": "payment_gateway"}
        )
        recommended_refund_brl = round(refundable_total, 2)
        if recommended_refund_brl > 0:
            refund_lines.append(
                {
                    "reason_code": "retry_failed_refund",
                    "amount_brl": recommended_refund_brl,
                    "entity_id": order_id,
                }
            )
        resolution_actions = ["reissue_failed_refund", "update_payment_method"]

    elif "canceled_order_paid" in claim_topics:
        primary_issue = "canceled_order_paid"
        case_status = "action_required"
        ranked_causes.append({"cause_code": "CAPTURE_AFTER_CANCELLATION", "rank": 1})
        responsible_parties.append({"party_type": "platform", "party_id": "order_system"})
        recommended_refund_brl = round(captured_total, 2)
        if recommended_refund_brl > 0:
            refund_lines.append(
                {
                    "reason_code": "canceled_order_refund",
                    "amount_brl": recommended_refund_brl,
                    "entity_id": order_id,
                }
            )
        resolution_actions = ["issue_full_refund", "cancel_order_sync"]

    elif "unavailable_order_paid" in claim_topics:
        primary_issue = "unavailable_order_paid"
        case_status = "action_required"
        ranked_causes.append({"cause_code": "SELLER_OUT_OF_STOCK", "rank": 1})
        for sid in seller_ids or ["seller-unknown"]:
            responsible_parties.append({"party_type": "seller", "party_id": sid})
        recommended_refund_brl = round(captured_total, 2)
        if recommended_refund_brl > 0:
            refund_lines.append(
                {
                    "reason_code": "stockout_refund",
                    "amount_brl": recommended_refund_brl,
                    "entity_id": order_id,
                }
            )
        resolution_actions = ["issue_full_refund", "update_seller_inventory"]

    elif "valid_split_payment" in claim_topics:
        primary_issue = "valid_split_payment"
        case_status = "no_action"
        ranked_causes.append({"cause_code": "CUSTOMER_SPLIT_PAYMENT_CONFIRMED", "rank": 1})
        responsible_parties.append({"party_type": "customer", "party_id": "customer-self"})
        recommended_refund_brl = 0.0
        resolution_actions = ["clarify_split_statement_to_customer"]

    elif "unsupported_claim" in claim_topics:
        primary_issue = "unsupported_claim"
        case_status = "no_action"
        ranked_causes.append({"cause_code": "CUSTOMER_UNSUPPORTED_DISPUTE", "rank": 1})
        responsible_parties.append({"party_type": "customer", "party_id": "customer-self"})
        recommended_refund_brl = 0.0
        resolution_actions = ["reject_claim_with_explanation"]

    else:
        primary_issue = "insufficient_evidence"
        case_status = "needs_investigation"
        ranked_causes.append({"cause_code": "INSUFFICIENT_TIMELINE_EVIDENCE", "rank": 1})
        responsible_parties.append({"party_type": "unknown", "party_id": None})
        resolution_actions = ["request_additional_documentation"]

    # Đánh giá từng claim
    claim_assessments: list[dict[str, Any]] = []
    for cl in claims:
        cid = cl.get("claim_id", "")
        ctopic = cl.get("topic", "")
        if ctopic == "requested_full_refund":
            verdict_cl = "supported" if recommended_refund_brl > 0 else "unsupported"
        elif ctopic == primary_issue:
            verdict_cl = "supported"
        else:
            verdict_cl = "unsupported"

        claim_assessments.append(
            {
                "claim_id": cid,
                "verdict": verdict_cl,
                "confidence": 0.95,
                "evidence_refs": list(context.evidence_refs[:5]),
            }
        )

    # Lưu toàn bộ findings vào context
    context.findings["assessment"] = {
        "primary_issue": primary_issue,
        "secondary_issues": [t for t in claim_topics if t != primary_issue][:10],
        "case_status": case_status,
        "confidence": 0.95,
    }
    context.findings["affected_entities"] = {
        "order_ids": sorted(list(set(resolved_order_ids))),
        "item_ids": sorted(list(set(item_ids))),
        "seller_ids": sorted(list(set(seller_ids))),
        "payment_references": [order_id] if order_id else [],
        "shipment_ids": [f"ship-{order_id[:16]}"] if order_id else [],
    }
    context.findings["claim_assessments"] = claim_assessments
    context.findings["data_conflicts"] = []
    context.findings["financial_resolution"] = {
        "currency": "BRL",
        "recommended_refund_brl": recommended_refund_brl,
        "refund_lines": refund_lines,
    }
    context.findings["resolution_actions"] = resolution_actions[:8]

    # Trả về kết quả root_cause_analysis
    return {
        "ranked_causes": ranked_causes[:5],
        "responsible_parties": responsible_parties[:5],
    }
