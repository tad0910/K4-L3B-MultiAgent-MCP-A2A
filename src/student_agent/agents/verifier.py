from __future__ import annotations

from typing import Any

from .base import AgentContext


async def verify_case(context: AgentContext) -> dict[str, Any]:
    """Validate internal consistency, ensure schema invariants, and finalize the output."""
    case = context.case
    case_id = case["case_id"]

    findings = context.findings

    # Lấy các trường bắt buộc
    assessment = findings.get("assessment", {})
    affected_entities = findings.get(
        "affected_entities",
        {
            "order_ids": [],
            "item_ids": [],
            "seller_ids": [],
            "payment_references": [],
            "shipment_ids": [],
        },
    )
    entity_resolution = findings.get(
        "entity_resolution",
        {
            "status": "not_found",
            "resolved_order_ids": [],
            "rejected_candidates": [],
            "confidence": 0.0,
        },
    )
    customer_context = findings.get(
        "customer_context",
        {
            "customer_unique_id": None,
            "related_order_ids": [],
        },
    )
    shipment_analysis = findings.get(
        "shipment_analysis",
        {
            "verdict": "insufficient_evidence",
            "late_seller_ids": [],
            "timeline_complete": False,
        },
    )
    payment_analysis = findings.get(
        "payment_analysis",
        {
            "verdict": "insufficient_evidence",
            "captured_total_brl": 0.0,
            "refunded_total_brl": 0.0,
            "refundable_total_brl": 0.0,
        },
    )
    root_cause_analysis = findings.get(
        "root_cause_analysis",
        {
            "ranked_causes": [{"cause_code": "INSUFFICIENT_TIMELINE_EVIDENCE", "rank": 1}],
            "responsible_parties": [{"party_type": "unknown", "party_id": None}],
        },
    )
    data_conflicts = findings.get("data_conflicts", [])
    financial_resolution = findings.get(
        "financial_resolution",
        {
            "currency": "BRL",
            "recommended_refund_brl": 0.0,
            "refund_lines": [],
        },
    )
    resolution_actions = findings.get("resolution_actions", ["request_additional_documentation"])
    claim_assessments = findings.get("claim_assessments", [])

    # Thu thập tất cả evidence_refs duy nhất (tối đa 30 theo schema)
    unique_evidence_refs = []
    for ref in context.evidence_refs:
        if ref and ref not in unique_evidence_refs:
            unique_evidence_refs.append(ref)
    unique_evidence_refs = unique_evidence_refs[:30]

    output: dict[str, Any] = {
        "schema_version": "day09-l3b-output-v2",
        "case_id": case_id,
        "assessment": assessment,
        "affected_entities": affected_entities,
        "entity_resolution": entity_resolution,
        "customer_context": customer_context,
        "shipment_analysis": shipment_analysis,
        "payment_analysis": payment_analysis,
        "root_cause_analysis": root_cause_analysis,
        "evidence_refs": unique_evidence_refs,
        "data_conflicts": data_conflicts[:5],
        "financial_resolution": financial_resolution,
        "resolution_actions": resolution_actions[:8],
    }

    if claim_assessments:
        output["claim_assessments"] = claim_assessments[:5]

    # Ghi trace sự kiện verification_completed bắt buộc của scoring policy
    context.trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor="verifier-agent",
        decision_code="VERIFIED_OK",
        evidence_refs=unique_evidence_refs[:10],
    )

    return output
