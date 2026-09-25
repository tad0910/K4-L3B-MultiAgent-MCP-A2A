from __future__ import annotations

from typing import Any

from .base import AgentContext, AgentResult


async def analyze_payment(context: AgentContext) -> AgentResult:
    """Reconcile captures, refunds and refundable amounts."""
    case = context.case
    case_id = case["case_id"]

    if not hasattr(context, "cache"):
        context.cache = {}
    cache: dict[str, Any] = context.cache

    entity_res = context.findings.get("entity_resolution", {})
    resolved_order_ids = entity_res.get("resolved_order_ids", [])

    if not resolved_order_ids:
        return {
            "verdict": "insufficient_evidence",
            "captured_total_brl": 0.0,
            "refunded_total_brl": 0.0,
            "refundable_total_brl": 0.0,
        }

    order_id = resolved_order_ids[0]

    # 1. Gọi get_payment_timeline
    pay_key = f"get_payment_timeline:[('case_id', '{case_id}'), ('order_id', '{order_id}')]"
    pay_data: dict[str, Any] = {}
    try:
        if pay_key in cache:
            pay_evidence = cache[pay_key]
        else:
            pay_evidence = await context.gateway.call(
                "get_payment_timeline", case_id=case_id, order_id=order_id
            )
            cache[pay_key] = pay_evidence

        evidence_ref = pay_evidence.get("evidence_ref")
        if evidence_ref:
            context.register_domain_evidence("payment", pay_evidence)
            context.trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="payment-agent",
                tool_name="get_payment_timeline",
                evidence_refs=[evidence_ref],
            )
        pay_data = pay_evidence.get("data", {})
    except Exception:
        pass

    # 2. Gọi get_refund_timeline (nếu có)
    ref_key = f"get_refund_timeline:[('case_id', '{case_id}'), ('order_id', '{order_id}')]"
    ref_data: dict[str, Any] = {}
    try:
        if ref_key in cache:
            ref_evidence = cache[ref_key]
        else:
            ref_evidence = await context.gateway.call(
                "get_refund_timeline", case_id=case_id, order_id=order_id
            )
            cache[ref_key] = ref_evidence

        evidence_ref = ref_evidence.get("evidence_ref")
        if evidence_ref:
            context.register_domain_evidence("payment", ref_evidence)
            context.trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="payment-agent",
                tool_name="get_refund_timeline",
                evidence_refs=[evidence_ref],
            )
        ref_data = ref_evidence.get("data", {})
    except Exception:
        pass

    # Tính toán captured_total_brl
    payments = pay_data.get("payments", [])
    pay_events = pay_data.get("events", [])

    captured_events = [
        e
        for e in pay_events
        if e.get("event_type") == "captured" and e.get("status") == "confirmed"
    ]
    if captured_events:
        captured_total = sum(float(e.get("amount_brl", 0.0)) for e in captured_events)
    elif payments:
        captured_total = sum(float(p.get("payment_value", 0.0)) for p in payments)
    else:
        captured_total = 0.0
    captured_total = round(captured_total, 2)

    # Tính toán refunded_total_brl
    refund_events = ref_data.get("events", [])
    refunded_total = 0.0
    has_refund_pending = False
    has_refund_failed = False

    for ref_ev in refund_events:
        status = ref_ev.get("status")
        amt = float(ref_ev.get("amount_brl", 0.0))
        if status in ("confirmed", "completed", "refunded"):
            refunded_total += amt
        elif status == "pending":
            has_refund_pending = True
        elif status == "failed":
            has_refund_failed = True
    refunded_total = round(refunded_total, 2)

    # Kiểm tra reconciliation_mismatch & duplicate_capture
    has_mismatch = any(e.get("event_type") == "reconciliation_mismatch" for e in pay_events)

    # Kiểm tra duplicate payment: ví dụ có nhiều dòng payment trùng lặp hoàn toàn
    has_duplicate = False
    if len(payments) > 1:
        signatures = [
            (p.get("payment_sequential"), p.get("payment_type"), p.get("payment_value"))
            for p in payments
        ]
        if len(signatures) != len(set(signatures)):
            has_duplicate = True

    # Xác định verdict
    if has_refund_failed:
        verdict = "refund_failed"
    elif has_refund_pending:
        verdict = "refund_pending"
    elif refunded_total > 0 and refunded_total >= captured_total:
        verdict = "refunded"
    elif has_duplicate:
        verdict = "duplicate_capture"
    elif has_mismatch:
        verdict = "capture_mismatch"
    elif captured_total > 0:
        verdict = "reconciled"
    else:
        verdict = "insufficient_evidence"

    refundable_total = round(max(0.0, captured_total - refunded_total), 2)

    return {
        "verdict": verdict,
        "captured_total_brl": captured_total,
        "refunded_total_brl": refunded_total,
        "refundable_total_brl": refundable_total,
    }
