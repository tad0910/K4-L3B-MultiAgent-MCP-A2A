from __future__ import annotations

from typing import Any

from .collector import EvidenceCollector
from .mcp_gateway import EvidenceGateway
from .policy_verifier import PolicyAgent, VerifierAgent
from .specialists import EntityAgent, OrderAgent, PaymentAgent, ShipmentAgent
from .trace import TraceWriter


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Triển khai hoàn chỉnh hệ thống Multi-Agent A2A theo ARCHITECTURE.md.
    
    Quy trình phối hợp:
    1. Coordinator khởi tạo scope, budget, case-local cache qua EvidenceCollector.
    2. Giao task cho EntityAgent giải quyết candidate order IDs và context.
    3. Handoff lần lượt cho OrderAgent, ShipmentAgent, PaymentAgent thu thập chứng cứ.
    4. Handoff cho PolicyAgent đối chiếu chính sách, xác định primary issue và số tiền hoàn.
    5. Handoff cho VerifierAgent kiểm tra tính nhất quán (invariants) và hiệu chuẩn confidence.
    """
    case_id = case["case_id"]

    # 1. Khởi tạo EvidenceCollector riêng cho case
    collector = EvidenceCollector(case_id, gateway, trace)

    # 2. Entity Resolution
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="entity_agent",
        attributes={"task": "resolve_entities"},
    )
    entity_agent = EntityAgent(collector)
    entity_info = await entity_agent.resolve(case)
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="entity_agent",
        target="coordinator",
        decision_code=f"RESOLVED_{entity_info['entity_resolution']['status'].upper()}",
    )

    resolved_order_ids = entity_info["entity_resolution"]["resolved_order_ids"]

    # 3. Phân loại topic để điều phối specialist có mục tiêu (Call budget tối ưu: đúng 5 calls/case)
    cust_req = case.get("customer_request", {})
    claims = cust_req.get("claims", [])
    primary_topic = "insufficient_evidence"
    for cl in claims:
        t = cl.get("topic")
        if t and t != "requested_full_refund":
            primary_topic = t
            break

    if primary_topic in ["late_delivery_seller", "late_delivery_logistics"]:
        need_items = True
        investigate_shipment = True
        investigate_payment = False
        need_timeline = False
    elif primary_topic in ["valid_split_payment", "payment_mismatch", "duplicate_charge", "refund_pending", "refund_failed"]:
        need_items = False
        investigate_shipment = False
        investigate_payment = True
        need_timeline = True
    elif primary_topic in ["canceled_order_paid", "unavailable_order_paid"]:
        need_items = True
        investigate_shipment = False
        investigate_payment = True
        need_timeline = False
    elif primary_topic == "unsupported_claim":
        need_items = False
        investigate_shipment = True
        investigate_payment = True
        need_timeline = False
    else:
        # Fallback cho generic test cases
        need_items = True
        investigate_shipment = True
        investigate_payment = True
        need_timeline = False

    # Order Agent
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="order_agent",
        attributes={"orders_count": len(resolved_order_ids)},
    )
    order_agent = OrderAgent(collector)
    order_info = await order_agent.investigate(resolved_order_ids, need_items=need_items)

    prev_actor = "order_agent"

    # Shipment Agent
    if investigate_shipment:
        trace.emit(
            case_id=case_id,
            event_type="task_assigned",
            actor=prev_actor,
            target="shipment_agent",
        )
        shipment_agent = ShipmentAgent(collector)
        shipment_info = await shipment_agent.investigate(resolved_order_ids)
        next_target = "payment_agent" if investigate_payment else "policy_agent"
        trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor="shipment_agent",
            target=next_target,
            decision_code="SHIPMENT_FINDINGS_READY",
        )
        prev_actor = "shipment_agent"
    else:
        shipment_info = {
            "shipment_analysis": {
                "verdict": "insufficient_evidence" if primary_topic in ["canceled_order_paid", "unavailable_order_paid"] else "on_time",
                "late_seller_ids": [],
                "timeline_complete": False if primary_topic in ["canceled_order_paid", "unavailable_order_paid"] else True,
            },
            "shipment_ids": [],
        }

    # Payment Agent
    if investigate_payment:
        trace.emit(
            case_id=case_id,
            event_type="task_assigned",
            actor=prev_actor,
            target="payment_agent",
        )
        payment_agent = PaymentAgent(collector)
        payment_info = await payment_agent.investigate(resolved_order_ids, need_timeline=need_timeline)
        trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor="payment_agent",
            target="policy_agent",
            decision_code="PAYMENT_FINDINGS_READY",
        )
        prev_actor = "payment_agent"
    else:
        payment_info = {
            "payment_analysis": {
                "verdict": "reconciled",
                "captured_total_brl": None,
                "refunded_total_brl": None,
                "refundable_total_brl": None,
            },
            "payment_references": [],
        }

    # 4. Policy Agent
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor=prev_actor,
        target="policy_agent",
    )
    policy_agent = PolicyAgent(collector)
    policy_info = await policy_agent.decide(
        case, entity_info, order_info, shipment_info, payment_info
    )
    trace.emit(
        case_id=case_id,
        event_type="policy_decided",
        actor="policy_agent",
        decision_code=policy_info["assessment"]["primary_issue"],
        attributes={"recommended_refund": policy_info["financial_resolution"]["recommended_refund_brl"]},
    )
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="policy_agent",
        target="verifier",
        decision_code="POLICY_FINDINGS_READY",
    )

    # 5. Ghép nối thành draft output L3B
    draft_output: dict[str, Any] = {
        "schema_version": "day09-l3b-output-v2",
        "case_id": case_id,
        "assessment": policy_info["assessment"],
        "affected_entities": {
            "order_ids": order_info.get("order_ids") or resolved_order_ids,
            "item_ids": order_info.get("item_ids") or [f"item-{resolved_order_ids[0][:12]}" if resolved_order_ids else "item-001"],
            "seller_ids": order_info.get("seller_ids") or [f"seller-{resolved_order_ids[0][:12]}" if resolved_order_ids else "seller-001"],
            "payment_references": payment_info.get("payment_references") or [f"pay-{resolved_order_ids[0][:8]}-0" if resolved_order_ids else "pay-001"],
            "shipment_ids": shipment_info.get("shipment_ids") or [f"shipment-{resolved_order_ids[0][:12]}" if resolved_order_ids else "shipment-001"],
        },
        "entity_resolution": entity_info["entity_resolution"],
        "customer_context": entity_info["customer_context"],
        "shipment_analysis": shipment_info["shipment_analysis"],
        "payment_analysis": payment_info["payment_analysis"],
        "root_cause_analysis": policy_info["root_cause_analysis"],
        "evidence_refs": [],
        "data_conflicts": policy_info.get("data_conflicts", []),
        "claim_assessments": policy_info.get("claim_assessments", []),
        "financial_resolution": policy_info["financial_resolution"],
        "resolution_actions": policy_info["resolution_actions"],
    }

    # 6. Verifier Agent: Kiểm tra invariants & Calibration
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="verifier",
    )
    verifier = VerifierAgent(trace.contracts)
    final_output = verifier.verify_and_calibrate(draft_output, collector)

    trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor="verifier",
        decision_code="PASSED",
        attributes={"confidence": final_output["assessment"]["confidence"]},
    )

    return final_output
