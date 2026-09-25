# L3B Architecture Record

Team phải cập nhật tài liệu này cùng source. Mục tiêu là mô tả quyết định có thể kiểm chứng, không ghi prompt bí mật hoặc chain-of-thought.

## 1. System overview

Vẽ hoặc mô tả luồng từ input/candidate resolution đến MCP investigation, specialist agents, conflict resolver, verifier, output và trace.

```text
Input → Entity Resolver → Coordinator → Specialists → Conflict Resolver → Verifier → Output
            │                              │                  │             │
            └──────────────────────────── MCP ────────────────┴──────────── Trace
```

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Tool permission | Output/handoff |
| --- | --- | --- | --- | --- |
| Entity/customer | TODO | TODO | TODO | TODO |
| Coordinator | TODO | TODO | TODO | TODO |
| Order/product | TODO | TODO | TODO | TODO |
| Shipment | TODO | TODO | TODO | TODO |
| Payment/refund | TODO | TODO | TODO | TODO |
| Policy | TODO | TODO | TODO | TODO |
| Conflict resolver | TODO | TODO | TODO | TODO |
| Verifier | TODO | TODO | TODO | TODO |

Áp dụng least privilege; tool discovery không đồng nghĩa mọi actor đều được gọi mọi tool.

## 3. Entity resolution và A2A protocol

Mô tả cách xếp hạng/reject candidate, confidence threshold, message envelope, correlation theo `case_id`, điều kiện handoff, timeout và cách tránh vòng lặp. Không trace nội dung suy luận riêng.

## 4. Evidence và conflict lifecycle

Mô tả cách validate MCP response, lưu `evidence_ref`, chọn source theo policy, biểu diễn unresolved conflict, map evidence vào claim/output và emit `tool_result_consumed`. Evidence không được tái sử dụng giữa các case.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event/code |
| --- | ---: | --- | --- |
| MCP timeout | 1 | Log warning, set verdict to `insufficient_evidence`, reduce confidence | `mcp_tool_timeout` |
| Entity not found/ambiguous | 0 | Set entity resolution status to `ambiguous`/`not_found`, move candidates to `rejected_candidates` | `entity_resolution_failed` |
| Source conflict | 0 | Canonical logistics/payment evidence prevails over seller claims; append to `data_conflicts` | `source_conflict_resolved` |
| Invalid specialist result | 1 | Re-evaluate with default schema values and fallback to `insufficient_evidence` | `specialist_validation_failed` |

### Query Budget & Caching Strategy
- **Least Privilege Tool Access**: Specialist agents (Shipment, Payment, Root Cause) only call tools strictly within their domain scope.
- **In-Memory Per-Case Caching**: MCP tool responses are cached by `(case_id, tool_name, hash(args))` within the scope of a single case execution to prevent duplicate calls and optimize efficiency score (5%).
- **Call Budget Limit**: Maximum 3 MCP tool calls per specialist agent per case (hard cap of 8 calls per case total).
- **Graceful Degradation**: Missing evidence or tool failures result in explicit `insufficient_evidence` verdicts instead of inventing or halluncinating unbacked data.

## 6. Verification invariants

Liệt kê kiểm tra trước finalize: schema, entity scope, rejected candidates, evidence ownership, claim linkage, timeline, payment/refund totals, source precedence, responsibility/action consistency và confidence bounds.

## 7. Reproducibility

Ghi model/config, dependency pinning, concurrency limit, random seed (nếu có), lệnh chạy và giới hạn tài nguyên. Không ghi API key.
