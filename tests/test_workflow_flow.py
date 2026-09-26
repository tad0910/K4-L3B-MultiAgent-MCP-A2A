import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contracts" / "schemas"


def test_solve_case_synthetic_flow(tmp_path: Path):
    async def _runner():
        contracts = Contracts(SCHEMAS)
        trace_path = tmp_path / "traces" / "trace.jsonl"
        trace = TraceWriter(trace_path, contracts)

        # Mock gateway
        gateway = MagicMock()

        async def mock_call(tool_name: str, case_id: str, **arguments):
            if tool_name == "get_customer_history":
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": "ev_customer_history_1234567890",
                    "result_hash": "sha256:" + "0" * 64,
                    "domain": "customer",
                    "data": {"orders": [{"order_id": "ORD_001"}]},
                }
            elif tool_name == "get_order":
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": "ev_order_detail_123456789012345",
                    "result_hash": "sha256:" + "0" * 64,
                    "domain": "order",
                    "data": {"order_id": "ORD_001", "status": "delivered"},
                }
            elif tool_name == "get_order_items":
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": "ev_order_items_1234567890123456",
                    "result_hash": "sha256:" + "0" * 64,
                    "domain": "order",
                    "data": {"items": [{"item_id": "ITEM_01", "seller_id": "SELLER_99"}]},
                }
            elif tool_name == "get_shipment_summary":
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": "ev_shipment_summary_123456789012",
                    "result_hash": "sha256:" + "0" * 64,
                    "domain": "shipment",
                    "data": {"verdict": "on_time", "shipment_id": "SHIP_123"},
                }
            elif tool_name == "get_order_payments":
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": "ev_order_payments_1234567890123",
                    "result_hash": "sha256:" + "0" * 64,
                    "domain": "payment",
                    "data": {"payments": [{"payment_reference": "PAY_REF_1", "payment_value": 150.0}]},
                }
            elif tool_name == "get_refund_timeline":
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": "ev_refund_timeline_123456789012",
                    "result_hash": "sha256:" + "0" * 64,
                    "domain": "payment",
                    "data": {"refunds": []},
                }
            elif tool_name == "get_product_context":
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": "ev_product_context_12345678901",
                    "result_hash": "sha256:" + "0" * 64,
                    "domain": "product",
                    "data": [],
                }
            elif tool_name == "get_sellers":
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": "ev_sellers_list_12345678901234",
                    "result_hash": "sha256:" + "0" * 64,
                    "domain": "seller",
                    "data": [{"seller_id": "SELLER_99"}],
                }
            elif tool_name == "get_payment_timeline":
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": "ev_payment_timeline_1234567890",
                    "result_hash": "sha256:" + "0" * 64,
                    "domain": "payment",
                    "data": {"events": []},
                }
            elif tool_name == "get_policy":
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": "ev_policy_dispute_1234567890123",
                    "result_hash": "sha256:" + "0" * 64,
                    "domain": "policy",
                    "data": {"policy_id": "POL_01", "rules": {}},
                }
            raise ValueError(f"Unknown mock tool: {tool_name}")

        gateway.call = AsyncMock(side_effect=mock_call)

        case = {
            "case_id": "CASE_TEST_001",
            "customer_unique_id": "CUST_999",
            "candidate_order_ids": ["ORD_001", "ORD_002"],
            "claim_type": "delivery_delay",
            "customer_claim": "The package arrived on time actually",
        }

        output = await solve_case(case, gateway, trace)

        # Kiểm tra contract output
        contracts.validate_output(output, "test_output")
        assert output["case_id"] == "CASE_TEST_001"
        assert output["schema_version"] == "day09-l3b-output-v2"
        assert len(output["evidence_refs"]) > 0
        assert "ev_order_detail_123456789012345" in output["evidence_refs"]
        assert output["entity_resolution"]["status"] == "resolved"
        assert "ORD_001" in output["entity_resolution"]["resolved_order_ids"]
        assert "ORD_002" in output["entity_resolution"]["rejected_candidates"]

    asyncio.run(_runner())
