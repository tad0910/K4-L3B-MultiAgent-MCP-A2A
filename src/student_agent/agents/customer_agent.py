from .base import AgentContext, AgentResult


async def collect_customer_context(context: AgentContext) -> AgentResult:
    """Investigate customer unique ID and history of related orders."""
    case = context.case

    # 1. Xác định customer_unique_id từ hint hoặc từ order data đã có trong cache
    customer_unique_id = case.get("customer_unique_id_hint")
    if not customer_unique_id and hasattr(context, "cache"):
        for k, v in context.cache.items():
            if k.startswith("get_order:"):
                cid = v.get("data", {}).get("customer_id")
                if cid:
                    customer_unique_id = cid
                    break

    # 2. Lấy các order_ids đã được entity_resolver tìm ra
    entity_res = context.findings.get("entity_resolution", {})
    resolved_order_ids = entity_res.get("resolved_order_ids", [])

    return {
        "customer_unique_id": customer_unique_id or "customer-default",
        "related_order_ids": sorted(list(set(resolved_order_ids))),
    }
