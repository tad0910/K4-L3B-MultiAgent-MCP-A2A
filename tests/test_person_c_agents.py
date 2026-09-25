from __future__ import annotations

from pathlib import Path
from student_agent.agents.shipment_agent import ShipmentAgent, analyze_shipment
from student_agent.agents.payment_agent import PaymentAgent, analyze_payment
from student_agent.agents.root_cause_agent import RootCauseAgent, analyze_root_cause
from student_agent.contracts import Contracts


def test_shipment_agent_seller_delay() -> None:
    order_data = {
        "shipping_limit_date": "2023-01-10T12:00:00Z",
        "order_delivered_carrier_date": "2023-01-12T12:00:00Z",  # Dispatched late by seller
        "order_estimated_delivery_date": "2023-01-20T12:00:00Z",
        "order_delivered_customer_date": "2023-01-18T12:00:00Z",
        "seller_ids": ["seller-99"],
    }
    result = analyze_shipment(case_id="TEST_001", order_data=order_data)
    assert result["verdict"] == "seller_delay"
    assert result["late_seller_ids"] == ["seller-99"]
    assert result["timeline_complete"] is True


def test_shipment_agent_logistics_delay() -> None:
    order_data = {
        "shipping_limit_date": "2023-01-10T12:00:00Z",
        "order_delivered_carrier_date": "2023-01-09T12:00:00Z",  # Seller on time
        "order_estimated_delivery_date": "2023-01-15T12:00:00Z",
        "order_delivered_customer_date": "2023-01-20T12:00:00Z",  # Delivered late by carrier
        "seller_ids": ["seller-99"],
    }
    result = analyze_shipment(case_id="TEST_002", order_data=order_data)
    assert result["verdict"] == "logistics_delay"
    assert result["late_seller_ids"] == []
    assert result["timeline_complete"] is True


def test_payment_agent_reconciled_and_refund() -> None:
    order_data = {
        "order_amount_brl": 150.00,
        "claimed_order_id": "order-123",
        "payments": [
            {
                "payment_reference": "tx-1",
                "status": "approved",
                "payment_value": 150.00,
            }
        ],
    }
    payment_analysis, financial_resolution = analyze_payment(
        case_id="TEST_003",
        order_data=order_data,
        primary_issue="late_delivery_seller",
    )
    assert payment_analysis["verdict"] == "reconciled"
    assert payment_analysis["captured_total_brl"] == 150.00
    assert payment_analysis["refundable_total_brl"] == 150.00
    assert financial_resolution["currency"] == "BRL"
    assert financial_resolution["recommended_refund_brl"] == 150.00
    assert len(financial_resolution["refund_lines"]) == 1
    assert financial_resolution["refund_lines"][0]["amount_brl"] == 150.00
    assert financial_resolution["refund_lines"][0]["entity_id"] == "order-123"


def test_payment_agent_duplicate_capture() -> None:
    order_data = {
        "order_amount_brl": 100.00,
        "payments": [
            {"payment_reference": "dup-tx", "status": "approved", "payment_value": 100.00},
            {"payment_reference": "dup-tx", "status": "approved", "payment_value": 100.00},
        ],
    }
    payment_analysis, financial_resolution = analyze_payment(
        case_id="TEST_004",
        order_data=order_data,
        primary_issue="duplicate_charge",
    )
    assert payment_analysis["verdict"] == "duplicate_capture"
    assert payment_analysis["captured_total_brl"] == 200.00


def test_root_cause_agent_schema_validity() -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")

    shipment = {"verdict": "seller_delay", "late_seller_ids": ["s1"], "timeline_complete": True}
    payment = {
        "verdict": "reconciled",
        "captured_total_brl": 100.0,
        "refunded_total_brl": 0.0,
        "refundable_total_brl": 100.0,
    }
    affected = {
        "order_ids": ["ord1"],
        "seller_ids": ["s1"],
        "item_ids": ["item1"],
        "payment_references": ["tx1"],
        "shipment_ids": ["ship1"],
    }
    evidence_list = [
        {"tool_name": "seller_db", "data": {"status": "shipped"}},
        {"tool_name": "logistics_api", "data": {"status": "delayed"}},
    ]

    rca, conflicts, actions = analyze_root_cause(
        case_id="TEST_005",
        primary_issue="late_delivery_seller",
        shipment_analysis=shipment,
        payment_analysis=payment,
        affected_entities=affected,
        evidence_list=evidence_list,
    )

    full_output = {
        "schema_version": "day09-l3b-output-v2",
        "case_id": "TEST_005",
        "assessment": {
            "primary_issue": "late_delivery_seller",
            "secondary_issues": [],
            "case_status": "action_required",
            "confidence": 0.95,
        },
        "affected_entities": affected,
        "entity_resolution": {
            "status": "resolved",
            "resolved_order_ids": ["ord1"],
            "rejected_candidates": [],
            "confidence": 0.95,
        },
        "customer_context": {
            "customer_unique_id": "cust1",
            "related_order_ids": ["ord1"],
        },
        "shipment_analysis": shipment,
        "payment_analysis": payment,
        "root_cause_analysis": rca,
        "evidence_refs": ["ev_123456789012345678901234"],
        "data_conflicts": conflicts,
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": 100.0,
            "refund_lines": [{"reason_code": "late_delivery_seller", "amount_brl": 100.0, "entity_id": "ord1"}],
        },
        "resolution_actions": actions,
    }

    contracts.validate_output(full_output, "test_output")
