from __future__ import annotations

from typing import Any

from .agents import (
    AgentContext,
    analyze_conflicts_and_root_cause,
    analyze_payment,
    analyze_shipment,
    collect_customer_context,
    resolve_entities,
    verify_case,
)
from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Coordinate the case-scoped specialist handoffs.

    Each agent writes findings into the shared context. The functions are intentionally
    stubs until their MCP queries and contract-shaped results are implemented.
    """
    context = AgentContext(case=case, gateway=gateway, trace=trace)
    case_id = case["case_id"]

    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="entity-agent")
    context.findings["entity_resolution"] = await resolve_entities(context)

    trace.emit(case_id=case_id, event_type="handoff", actor="entity-agent", target="customer-agent")
    context.findings["customer_context"] = await collect_customer_context(context)

    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="shipment-agent")
    context.findings["shipment_analysis"] = await analyze_shipment(context)

    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="payment-agent")
    context.findings["payment_analysis"] = await analyze_payment(context)

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="coordinator",
        target="conflict-root-cause-agent",
    )
    context.findings["root_cause_analysis"] = await analyze_conflicts_and_root_cause(context)

    trace.emit(case_id=case_id, event_type="handoff", actor="coordinator", target="verifier-agent")
    return await verify_case(context)
