from __future__ import annotations

from typing import Any


class RootCauseAgent:
    """Agent responsible for conflict resolution, root cause analysis, responsible party identification, and action creation.

    Resolves conflicting evidence from multiple MCP tools, maps issues to standardized cause codes,
    identifies responsible parties, and recommends resolution actions.
    """

    def __init__(self, trace_writer: Any | None = None) -> None:
        self.trace_writer = trace_writer

    def analyze(
        self,
        case_id: str,
        primary_issue: str,
        shipment_analysis: dict[str, Any] | None = None,
        payment_analysis: dict[str, Any] | None = None,
        affected_entities: dict[str, Any] | None = None,
        evidence_list: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
        """Perform root cause analysis and resolution planning.

        Args:
            case_id: Case ID under investigation.
            primary_issue: Determined primary issue string.
            shipment_analysis: Output from shipment agent.
            payment_analysis: Output from payment agent.
            affected_entities: Dict of entity ID sets (sellers, order_ids, etc.).
            evidence_list: Collected MCP evidence items for conflict checking.

        Returns:
            Tuple of (root_cause_analysis dict, data_conflicts list, resolution_actions list):
            - root_cause_analysis:
                {
                    "ranked_causes": [{"cause_code": str, "rank": int}, ...],
                    "responsible_parties": [{"party_type": str, "party_id": str | None}, ...]
                }
            - data_conflicts: list of data conflict objects
            - resolution_actions: list of action strings
        """
        shipment_analysis = shipment_analysis or {}
        payment_analysis = payment_analysis or {}
        affected_entities = affected_entities or {}
        evidence_list = evidence_list or []

        ranked_causes: list[dict[str, Any]] = []
        responsible_parties: list[dict[str, Any]] = []
        resolution_actions: list[str] = []
        data_conflicts: list[dict[str, Any]] = []

        # Extract entity IDs
        seller_ids = affected_entities.get("seller_ids", [])
        primary_seller_id = seller_ids[0] if seller_ids else None
        order_ids = affected_entities.get("order_ids", [])
        primary_order_id = order_ids[0] if order_ids else None

        # 1. Map Primary Issue to Root Cause Codes & Responsible Parties
        if primary_issue == "late_delivery_seller":
            ranked_causes.append({"cause_code": "SELLER_DISPATCH_DELAY", "rank": 1})
            responsible_parties.append({"party_type": "seller", "party_id": primary_seller_id})
            resolution_actions.append("FLAG_SELLER_LATE_DISPATCH")
            resolution_actions.append("ISSUE_REFUND_TO_CUSTOMER")

        elif primary_issue == "late_delivery_logistics":
            ranked_causes.append({"cause_code": "LOGISTICS_TRANSIT_DELAY", "rank": 1})
            responsible_parties.append({"party_type": "logistics_provider", "party_id": None})
            resolution_actions.append("FILE_CARRIER_CLAIM")
            resolution_actions.append("ISSUE_REFUND_TO_CUSTOMER")

        elif primary_issue == "canceled_order_paid":
            ranked_causes.append({"cause_code": "CANCELLED_ORDER_UNCAPTURED_REFUND", "rank": 1})
            responsible_parties.append({"party_type": "platform", "party_id": None})
            resolution_actions.append("PROCESS_AUTOMATED_REFUND")

        elif primary_issue == "unavailable_order_paid":
            ranked_causes.append({"cause_code": "OUT_OF_STOCK_AFTER_PAYMENT", "rank": 1})
            responsible_parties.append({"party_type": "seller", "party_id": primary_seller_id})
            resolution_actions.append("NOTIFY_SELLER_INVENTORY_MISMATCH")
            resolution_actions.append("ISSUE_REFUND_TO_CUSTOMER")

        elif primary_issue == "duplicate_charge":
            ranked_causes.append({"cause_code": "PAYMENT_DUPLICATE_CHARGE", "rank": 1})
            responsible_parties.append({"party_type": "payment_provider", "party_id": None})
            resolution_actions.append("REVERSE_DUPLICATE_TRANSACTION")

        elif primary_issue == "payment_mismatch":
            ranked_causes.append({"cause_code": "PAYMENT_AMOUNT_MISMATCH", "rank": 1})
            responsible_parties.append({"party_type": "customer", "party_id": None})
            resolution_actions.append("RECONCILE_PAYMENT_DIFFERENCE")

        elif primary_issue in ("refund_pending", "refund_failed"):
            ranked_causes.append({"cause_code": "REFUND_PROCESSING_FAILURE", "rank": 1})
            responsible_parties.append({"party_type": "payment_provider", "party_id": None})
            resolution_actions.append("RETRY_REFUND_TRANSACTION")

        elif primary_issue == "unsupported_claim":
            ranked_causes.append({"cause_code": "CLAIM_NOT_SUPPORTED_BY_EVIDENCE", "rank": 1})
            responsible_parties.append({"party_type": "customer", "party_id": None})
            resolution_actions.append("REJECT_CUSTOMER_CLAIM")

        else:  # insufficient_evidence or unknown
            ranked_causes.append({"cause_code": "INSUFFICIENT_EVIDENCE", "rank": 1})
            responsible_parties.append({"party_type": "unknown", "party_id": None})
            resolution_actions.append("REQUEST_ADDITIONAL_EVIDENCE")

        # 2. Check for secondary root causes
        shipment_verdict = shipment_analysis.get("verdict")
        if shipment_verdict == "seller_delay" and primary_issue != "late_delivery_seller":
            if len(ranked_causes) < 5:
                ranked_causes.append({"cause_code": "SELLER_DISPATCH_DELAY", "rank": len(ranked_causes) + 1})
            if not any(p["party_type"] == "seller" for p in responsible_parties):
                responsible_parties.append({"party_type": "seller", "party_id": primary_seller_id})

        payment_verdict = payment_analysis.get("verdict")
        if payment_verdict == "duplicate_capture" and primary_issue != "duplicate_charge":
            if len(ranked_causes) < 5:
                ranked_causes.append({"cause_code": "PAYMENT_DUPLICATE_CHARGE", "rank": len(ranked_causes) + 1})
            if not any(p["party_type"] == "payment_provider" for p in responsible_parties):
                responsible_parties.append({"party_type": "payment_provider", "party_id": None})

        # 3. Detect and resolve data conflicts across evidence sources
        conflict_fields: dict[str, set[str]] = {}
        for ev in evidence_list:
            if not isinstance(ev, dict):
                continue
            data = ev.get("data", ev)
            if not isinstance(data, dict):
                continue
            for field in ("status", "shipment_status", "payment_status"):
                val = data.get(field)
                if val:
                    source = str(ev.get("tool_name") or ev.get("source") or "mcp_tool")
                    if field not in conflict_fields:
                        conflict_fields[field] = set()
                    conflict_fields[field].add(f"{source}:{val}")

        for field, values in conflict_fields.items():
            if len(values) > 1:
                sources = sorted(list({v.split(":")[0] for v in values}))
                if len(sources) >= 2:
                    data_conflicts.append({
                        "field": field,
                        "sources": sources[:5],
                        "selected_source": sources[0],
                        "resolution_code": "CANONICAL_SOURCE_PREVAILS",
                    })

        # Limit sizes according to schema constraints
        root_cause_analysis = {
            "ranked_causes": ranked_causes[:5],
            "responsible_parties": responsible_parties[:5],
        }
        data_conflicts = data_conflicts[:5]

        # Ensure unique resolution actions (max 8, max length 80)
        unique_actions: list[str] = []
        for act in resolution_actions:
            clean_act = act[:80]
            if clean_act not in unique_actions and len(unique_actions) < 8:
                unique_actions.append(clean_act)

        if not unique_actions:
            unique_actions.append("CLOSE_INVESTIGATION")

        if self.trace_writer:
            self.trace_writer.emit(
                case_id=case_id,
                event_type="agent_execution",
                actor="conflict-root-cause-agent",
                decision_code=f"primary_cause:{ranked_causes[0]['cause_code'] if ranked_causes else 'NONE'}",
                attributes={
                    "causes_count": len(ranked_causes),
                    "conflicts_count": len(data_conflicts),
                    "actions_count": len(unique_actions),
                },
            )

        return root_cause_analysis, data_conflicts, unique_actions


# Aliases to satisfy both class name standards
ConflictRootCauseAgent = RootCauseAgent


def analyze_root_cause(
    case_id: str,
    primary_issue: str,
    shipment_analysis: dict[str, Any] | None = None,
    payment_analysis: dict[str, Any] | None = None,
    affected_entities: dict[str, Any] | None = None,
    evidence_list: list[dict[str, Any]] | None = None,
    trace_writer: Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Helper function to execute root cause analysis."""
    agent = RootCauseAgent(trace_writer=trace_writer)
    return agent.analyze(
        case_id=case_id,
        primary_issue=primary_issue,
        shipment_analysis=shipment_analysis,
        payment_analysis=payment_analysis,
        affected_entities=affected_entities,
        evidence_list=evidence_list,
    )
