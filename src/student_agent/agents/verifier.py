from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from .base import AgentContext


class VerificationError(ValueError):
    """Raised when a case cannot safely be finalized."""


def _refs(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {ref for item in value.values() for ref in _refs(item)}
    if isinstance(value, list):
        return {ref for item in value for ref in _refs(item)}
    if isinstance(value, str) and value.startswith("ev_"):
        return {value}
    return set()


def _ids(value: Any) -> set[str]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return set()


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=0.01)


class Verifier:
    """Validate a complete case result before the coordinator persists it."""

    def __init__(self, context: AgentContext) -> None:
        self.context = context
        self.case = context.case
        self.findings = context.findings

    def _required_findings(self) -> None:
        required = {
            "assessment",
            "affected_entities",
            "entity_resolution",
            "customer_context",
            "shipment_analysis",
            "payment_analysis",
            "root_cause_analysis",
            "financial_resolution",
            "resolution_actions",
        }
        missing = sorted(required - self.findings.keys())
        if missing:
            raise VerificationError(f"missing specialist findings: {', '.join(missing)}")

    def _entity_scope(self, output: dict[str, Any]) -> None:
        resolution = output["entity_resolution"]
        resolved = _ids(resolution["resolved_order_ids"])
        rejected = _ids(resolution["rejected_candidates"])
        if resolved & rejected:
            raise VerificationError("an order cannot be both resolved and rejected")

        candidates = _ids(self.case.get("candidate_order_ids"))
        if not resolved <= candidates:
            raise VerificationError(
                "resolved_order_ids contains an order outside candidate_order_ids"
            )
        affected_orders = _ids(output["affected_entities"]["order_ids"])
        related_orders = _ids(output["customer_context"]["related_order_ids"])
        if not affected_orders <= resolved:
            raise VerificationError("affected order is outside resolved_order_ids")
        if not related_orders <= resolved:
            raise VerificationError("customer history order is outside resolved_order_ids")
        if resolution["status"] == "resolved" and not resolved:
            raise VerificationError("resolved status requires at least one resolved order")
        if resolution["status"] == "not_found" and resolved:
            raise VerificationError("not_found status cannot contain resolved orders")

    def _evidence_scope(self, output: dict[str, Any]) -> set[str]:
        if not output.get("evidence_refs"):
            raise VerificationError("case output must contain at least one evidence_ref")
        declared = _refs(output)
        registered = set(self.context.evidence)
        missing = sorted(declared - registered)
        if missing:
            raise VerificationError(
                f"output references unregistered evidence: {', '.join(missing)}"
            )
        if set(self.context.evidence_refs) != registered:
            raise VerificationError("evidence_refs registry is inconsistent")
        return declared

    def _claims(self, output: dict[str, Any], evidence_refs: set[str]) -> None:
        claims = self.case.get("customer_request", {}).get("claims", [])
        claim_ids = {claim.get("claim_id") for claim in claims if isinstance(claim, dict)}
        assessments = output.get("claim_assessments", [])
        assessed_ids = {item.get("claim_id") for item in assessments}
        if not assessed_ids <= claim_ids:
            raise VerificationError("claim assessment contains an unknown claim_id")
        for assessment in assessments:
            if not (_refs(assessment) & evidence_refs):
                raise VerificationError(f"claim has no evidence: {assessment.get('claim_id')}")

    def _financials(self, output: dict[str, Any]) -> None:
        payment = output["payment_analysis"]
        captured = payment["captured_total_brl"]
        refunded = payment["refunded_total_brl"]
        refundable = payment["refundable_total_brl"]
        amounts = (
            ("captured_total_brl", captured),
            ("refunded_total_brl", refunded),
            ("refundable_total_brl", refundable),
        )
        for name, value in amounts:
            if value is not None and (not math.isfinite(value) or value < 0):
                raise VerificationError(f"invalid payment amount: {name}")
        if captured is not None and refunded is not None and refunded > captured + 0.01:
            raise VerificationError("refunded_total_brl exceeds captured_total_brl")
        if captured is not None and refundable is not None and refundable > captured + 0.01:
            raise VerificationError("refundable_total_brl exceeds captured_total_brl")

        financial = output["financial_resolution"]
        lines = financial["refund_lines"]
        line_total = sum(line["amount_brl"] for line in lines)
        if not _close(line_total, financial["recommended_refund_brl"]):
            raise VerificationError("refund lines do not sum to recommended_refund_brl")
        if refundable is not None and financial["recommended_refund_brl"] > refundable + 0.01:
            raise VerificationError("recommended refund exceeds refundable_total_brl")

    def _consistency(self, output: dict[str, Any]) -> None:
        confidence_values: Iterable[Any] = [
            output["assessment"]["confidence"],
            output["entity_resolution"]["confidence"],
            *(item["confidence"] for item in output.get("claim_assessments", [])),
        ]
        if any(
            not isinstance(value, (int, float)) or not 0 <= value <= 1
            for value in confidence_values
        ):
            raise VerificationError("confidence must be between 0 and 1")
        conflicts = output["data_conflicts"]
        for conflict in conflicts:
            if (
                conflict["selected_source"] is not None
                and conflict["selected_source"] not in conflict["sources"]
            ):
                raise VerificationError("selected conflict source is not listed in sources")
        ranks = [cause["rank"] for cause in output["root_cause_analysis"]["ranked_causes"]]
        if len(ranks) != len(set(ranks)):
            raise VerificationError("root causes contain duplicate ranks")

        # Ràng buộc nhất quán giữa case_status và financial_resolution
        status = output["assessment"]["case_status"]
        financial = output["financial_resolution"]
        if status == "no_action" and (
            financial["recommended_refund_brl"] > 0 or len(financial["refund_lines"]) > 0
        ):
            raise VerificationError("no_action case cannot have refund or refund lines")
        if status == "action_required" and len(output["resolution_actions"]) == 0:
            raise VerificationError("action_required case must have resolution actions")

        # Ràng buộc nhất quán giữa primary_issue và responsible_parties
        primary = output["assessment"]["primary_issue"]
        parties = [p["party_type"] for p in output["root_cause_analysis"]["responsible_parties"]]
        if primary == "late_delivery_seller" and "logistics_provider" in parties:
            raise VerificationError(
                "late_delivery_seller cannot assign responsibility to logistics"
            )
        if primary == "late_delivery_logistics" and "seller" in parties:
            raise VerificationError(
                "late_delivery_logistics cannot assign responsibility to seller"
            )

    def verify(self) -> dict[str, Any]:
        self._required_findings()
        output = {"schema_version": "day09-l3b-output-v2", "case_id": self.case["case_id"]}
        output.update(self.findings)
        output["evidence_refs"] = sorted(self.context.evidence)
        self.context.trace.contracts.validate_output(
            output, f"case {self.case['case_id']} verification"
        )
        if output["case_id"] != self.case["case_id"]:
            raise VerificationError("output case_id does not match input case_id")
        self._entity_scope(output)
        evidence_refs = self._evidence_scope(output)
        self._claims(output, evidence_refs)
        self._financials(output)
        self._consistency(output)
        self.context.trace.emit(
            case_id=self.case["case_id"],
            event_type="verification_completed",
            actor="verifier-agent",
            decision_code="VERIFIED_OK",
            evidence_refs=sorted(evidence_refs)[:20],
        )
        return output


async def verify_case(context: AgentContext) -> dict[str, Any]:
    return Verifier(context).verify()
