from __future__ import annotations

from typing import Any


class PaymentAgent:
    """Agent responsible for payment reconciliation, duplicate charge detection, and financial refund calculations.

    Reconciles order totals against captured payment transactions, detects duplicate charges,
    calculates refunded/refundable totals, and constructs financial resolution refund lines.
    """

    def __init__(self, trace_writer: Any | None = None) -> None:
        self.trace_writer = trace_writer

    def analyze(
        self,
        case_id: str,
        order_data: dict[str, Any] | None = None,
        payment_evidence: dict[str, Any] | list[dict[str, Any]] | None = None,
        primary_issue: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Perform payment audit and financial resolution.

        Args:
            case_id: Case ID under investigation.
            order_data: Order details payload containing order price, items, and payment info.
            payment_evidence: Evidence block or list of evidence blocks for payments/refunds.
            primary_issue: Determined primary issue for the case (used for refund line reasons).

        Returns:
            Tuple of (payment_analysis dict, financial_resolution dict):
            - payment_analysis:
                {
                    "verdict": "reconciled" | "capture_mismatch" | "duplicate_capture" | "refund_pending" | "refund_failed" | "refunded" | "insufficient_evidence",
                    "captured_total_brl": float | None,
                    "refunded_total_brl": float | None,
                    "refundable_total_brl": float | None
                }
            - financial_resolution:
                {
                    "currency": "BRL",
                    "recommended_refund_brl": float,
                    "refund_lines": list[dict[str, Any]]
                }
        """
        order_data = order_data or {}
        evidence_data: dict[str, Any] = {}
        evidence_refs: list[str] = []

        if isinstance(payment_evidence, dict):
            evidence_data = payment_evidence.get("data", payment_evidence)
            if "evidence_ref" in payment_evidence:
                evidence_refs.append(payment_evidence["evidence_ref"])
        elif isinstance(payment_evidence, list):
            for item in payment_evidence:
                if isinstance(item, dict):
                    if "evidence_ref" in item:
                        evidence_refs.append(item["evidence_ref"])
                    data_block = item.get("data", item)
                    if isinstance(data_block, dict):
                        evidence_data.update(data_block)

        combined = {**order_data, **evidence_data}

        # Extract payment transactions
        payments = combined.get("payments") or combined.get("payment_records") or combined.get("transactions") or []
        if not isinstance(payments, list):
            payments = []

        # Extract expected order total amount
        order_amount_brl = 0.0
        if "order_amount_brl" in combined and combined["order_amount_brl"] is not None:
            order_amount_brl = float(combined["order_amount_brl"])
        elif "total_amount" in combined and combined["total_amount"] is not None:
            order_amount_brl = float(combined["total_amount"])
        elif "price" in combined and combined["price"] is not None:
            order_amount_brl = float(combined["price"])
        else:
            # Calculate from items if available
            items = combined.get("items") or combined.get("order_items") or []
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        price = float(item.get("price", 0.0))
                        freight = float(item.get("freight_value", 0.0))
                        order_amount_brl += (price + freight)

        captured_total = 0.0
        refunded_total = 0.0
        has_duplicate_capture = False
        seen_tx_ids: set[str] = set()

        for p in payments:
            if not isinstance(p, dict):
                continue
            tx_id = str(p.get("payment_reference") or p.get("transaction_id") or p.get("payment_id") or "")
            status = str(p.get("status") or p.get("payment_status") or "captured").lower()
            val = float(p.get("payment_value") or p.get("amount") or p.get("captured_amount") or 0.0)

            if tx_id:
                if tx_id in seen_tx_ids and status in ("approved", "captured", "paid", "success"):
                    has_duplicate_capture = True
                seen_tx_ids.add(tx_id)

            if status in ("approved", "captured", "paid", "success", "reconciled"):
                captured_total += val
            elif status in ("refunded", "partially_refunded"):
                refunded_total += val

        # Explicit refund fields in evidence
        if "refunded_total_brl" in combined and combined["refunded_total_brl"] is not None:
            refunded_total = float(combined["refunded_total_brl"])
        if "captured_total_brl" in combined and combined["captured_total_brl"] is not None:
            captured_total = float(combined["captured_total_brl"])

        # Determine refundable total BRL
        refundable_total = max(0.0, round(captured_total - refunded_total, 2))

        # Check payment verdict
        verdict = "insufficient_evidence"

        payment_status_flag = (combined.get("payment_status") or combined.get("refund_status") or "").lower()

        if has_duplicate_capture or combined.get("is_duplicate_charge"):
            verdict = "duplicate_capture"
        elif payment_status_flag == "refund_pending":
            verdict = "refund_pending"
        elif payment_status_flag == "refund_failed":
            verdict = "refund_failed"
        elif refunded_total > 0 and refundable_total == 0:
            verdict = "refunded"
        elif captured_total > 0:
            if order_amount_brl > 0 and abs(captured_total - order_amount_brl) > 0.01:
                verdict = "capture_mismatch"
            else:
                verdict = "reconciled"
        elif not payments and not evidence_data:
            verdict = "insufficient_evidence"

        # Determine financial resolution refund recommendations
        recommended_refund_brl = 0.0
        refund_lines: list[dict[str, Any]] = []
        target_entity_id = combined.get("order_id") or combined.get("claimed_order_id")

        should_refund = primary_issue in (
            "canceled_order_paid",
            "unavailable_order_paid",
            "duplicate_charge",
            "late_delivery_seller",
            "late_delivery_logistics",
            "payment_mismatch",
            "refund_pending",
            "refund_failed",
        ) or verdict in ("duplicate_capture", "capture_mismatch", "refund_pending", "refund_failed")

        if should_refund and refundable_total > 0:
            recommended_refund_brl = refundable_total
            reason = (primary_issue or verdict or "order_claim_resolution").lower()
            refund_lines.append({
                "reason_code": reason[:80],
                "amount_brl": round(recommended_refund_brl, 2),
                "entity_id": str(target_entity_id) if target_entity_id else None,
            })

        payment_analysis = {
            "verdict": verdict,
            "captured_total_brl": round(captured_total, 2) if captured_total >= 0 else None,
            "refunded_total_brl": round(refunded_total, 2) if refunded_total >= 0 else None,
            "refundable_total_brl": round(refundable_total, 2) if refundable_total >= 0 else None,
        }

        financial_resolution = {
            "currency": "BRL",
            "recommended_refund_brl": round(recommended_refund_brl, 2),
            "refund_lines": refund_lines,
        }

        if self.trace_writer:
            self.trace_writer.emit(
                case_id=case_id,
                event_type="agent_execution",
                actor="payment-agent",
                decision_code=f"payment_verdict:{verdict}",
                evidence_refs=evidence_refs if evidence_refs else None,
                attributes={
                    "captured_brl": captured_total,
                    "recommended_refund_brl": recommended_refund_brl,
                },
            )

        return payment_analysis, financial_resolution


def analyze_payment(
    case_id: str,
    order_data: dict[str, Any] | None = None,
    payment_evidence: dict[str, Any] | list[dict[str, Any]] | None = None,
    primary_issue: str | None = None,
    trace_writer: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Helper function to execute payment analysis."""
    agent = PaymentAgent(trace_writer=trace_writer)
    return agent.analyze(
        case_id=case_id,
        order_data=order_data,
        payment_evidence=payment_evidence,
        primary_issue=primary_issue,
    )
