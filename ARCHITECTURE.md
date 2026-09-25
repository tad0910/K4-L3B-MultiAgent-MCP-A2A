# L3B Architecture Record

Tài liệu này mô tả các quyết định có thể kiểm chứng của workflow. Không ghi prompt bí mật,
chain-of-thought hoặc API key.

## 1. System overview

Mỗi case được xử lý độc lập. Coordinator tạo `AgentContext` mới, truyền context qua các
specialist và chỉ trả output sau khi Verifier vượt qua toàn bộ hard-gates.

```text
Input
    -> Entity Resolver
    -> Customer Context
    -> Shipment + Payment Specialists
    -> Conflict / Root Cause Resolver
    -> Verifier hard-gates
    -> Output JSON
```

MCP là nguồn evidence duy nhất cho dữ liệu nghiệp vụ. `TraceWriter` ghi các sự kiện quan
sát được (`task_assigned`, `handoff`, `tool_result_consumed`, `verification_completed`),
không ghi nội dung suy luận riêng.

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Tool permission | Output/handoff |
| --- | --- | --- | --- | --- |
| Entity Resolver | `claimed_order_id`, `candidate_order_ids`, customer hint | Xếp hạng candidate, xác định resolved/rejected order | Order lookup và các tool định danh cần thiết | `entity_resolution`, affected order scope -> Customer/Coordinator |
| Customer Agent | Resolved order/customer identity | Lấy customer history và related orders | Customer history | `customer_context` -> Coordinator |
| Coordinator | Case input và specialist findings | Tạo context, phân công, giới hạn scope/budget, ghi trace | Discovery; không tự suy đoán evidence | Handoff giữa agents -> Verifier |
| Shipment Agent | Resolved order IDs | Dựng timeline, phân biệt seller/logistics delay | Shipment/order evidence | `shipment_analysis` |
| Payment Agent | Resolved order IDs và claims | Đối soát capture, refund, refundable amount | Payment/refund evidence | `payment_analysis`, financial facts |
| Conflict/Root Cause Agent | Các specialist findings | Chọn source theo policy, ghi unresolved conflicts, xếp hạng nguyên nhân | Policy và evidence cần đối chiếu | `root_cause_analysis`, `data_conflicts`, assessment |
| Verifier | Toàn bộ findings và case-scoped evidence registry | Schema validation, consistency, evidence cross-check, hard-gates | Không gọi tool mới | Final output hoặc `VerificationError` |

Áp dụng least privilege: tool discovery không cấp quyền gọi mọi tool. Agent chỉ gọi tool
phù hợp với domain và luôn truyền đúng `case_id`.

## 3. Entity resolution và A2A protocol

`case_id` là correlation key của toàn bộ handoff, trace event và MCP call. Entity Resolver
phải đánh giá `claimed_order_id` cùng candidate list:

- **Bộ lọc cú pháp & Pre-filter**: Kiểm tra định dạng Regex hex 32 ký tự (`^[0-9a-fA-F]{32}$`). Các mã giả lập, synthetic candidates (như `candidate-001`) được loại trừ ngay lập tức để tiết kiệm Call Budget.
- **Thứ tự ưu tiên**: `claimed_order_id` (nếu nằm trong candidate list) được xếp ưu tiên số 1 để xác thực trước qua tool `get_order`.
- **Kết quả trả về**:
  - `resolved_order_ids`: candidate có evidence hỗ trợ từ cơ sở dữ liệu;
  - `rejected_candidates`: candidate bị loại có lý do trong specialist state;
  - `status`: `resolved`, `ambiguous` hoặc `not_found`;
  - `confidence`: số trong `[0, 1]` (0.98 nếu khớp claimed order, 0.90 nếu resolved khác, 0.10 nếu not found).

Không agent nào được đưa order ngoài `candidate_order_ids` vào output. Customer, shipment,
payment và root-cause chỉ được chạy trên resolved scope. Handoff là một chiều theo pipeline,
không retry vòng lặp giữa agents (DAG tuyến tính). Mỗi MCP call phải tạo evidence response hợp lệ trước khi
được specialist dùng.

## 4. Evidence và conflict lifecycle

`EvidenceGateway.call()` validate mọi response bằng `mcp-evidence-response-v1.schema.json`.
Sau mỗi call thành công, agent phải đăng ký response vào context:

```python
evidence = await context.gateway.call(
    "get_customer_history",
    case_id=context.case["case_id"],
    customer_unique_id=customer_unique_id,
)
context.register_evidence(evidence)
context.trace.emit(
    case_id=context.case["case_id"],
    event_type="tool_result_consumed",
    actor="customer-agent",
    tool_name="get_customer_history",
    evidence_refs=[evidence["evidence_ref"]],
)
```

Evidence registry nằm trong `AgentContext`, nên không được tái sử dụng giữa các case.
`register_evidence()` từ chối cùng `evidence_ref` nếu `result_hash` thay đổi. Verifier kiểm
tra mọi `evidence_ref` trong output/claim đều có trong registry và registry không bị lệch.
Conflict phải chứa ít nhất hai source, `selected_source` phải nằm trong danh sách source;
nếu không chọn được thì để `null` và dùng `resolution_code` mô tả unresolved conflict.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event/code |
| --- | ---: | --- | --- |
| MCP timeout/error | 1 retry cho call idempotent | Dừng specialist nếu vẫn lỗi; không bịa evidence | `tool_result_consumed` chỉ khi thành công |
| Entity not found/ambiguous | 0 | Trả trạng thái tương ứng, chuyển `needs_investigation` | `handoff` |
| Source conflict | 0 | Ghi `data_conflicts`, không tự chọn ngoài policy | `policy_decided` |
| Invalid specialist result | 0 | Verifier reject case | `verification_completed` chỉ khi pass |
| Evidence không đăng ký/khác hash | 0 | Verifier reject case | `VerificationError` |

Cache chỉ tồn tại trong một `AgentContext` và khóa theo `(case_id, tool_name, arguments)`.
Không quét rộng hoặc gọi lại cùng một query khi evidence đã có. Không biến missing evidence
thành giá trị fallback.

## 6. Verification invariants and hard-gates

Trước khi output được ghi vào `outputs/<case_id>.json`, `Verifier` kiểm tra theo thứ tự:

1. Đủ toàn bộ specialist findings và output pass `l3b-output-v2.schema.json`.
2. `case_id` output khớp input; resolved/rejected không giao nhau.
3. Resolved orders thuộc candidate list; affected orders và related customer orders thuộc resolved scope.
4. Trạng thái entity hợp lý: `resolved` phải có order, `not_found` không được có order.
5. Mọi evidence ref trong output và claim đều tồn tại trong registry của chính case đó.
6. Claim assessment chỉ tham chiếu claim có trong input và mỗi claim assessment có evidence.
7. Capture, refund, refundable và recommended refund không âm; refund không vượt capture/refundable.
8. Tổng `refund_lines.amount_brl` khớp `recommended_refund_brl` trong sai số `0.01` BRL.
9. Confidence nằm trong `[0, 1]`, conflict source hợp lệ và root-cause rank không trùng.

Chỉ khi tất cả gate pass, Verifier emit `verification_completed` với code `VERIFIED_OK`
và trả output cho CLI. Exception trước bước ghi file khiến output không được lưu.

## 7. Reproducibility

- Python: `>=3.11`; dependencies được pin theo khoảng trong `pyproject.toml`.
- Input inventory: `case-set.json` và đúng 100 file `inputs/L3B_CASE_*.json`.
- Concurrency: mặc định tuần tự theo case; specialist song song chỉ được thêm khi vẫn giữ
    context/evidence registry riêng và không vượt query budget.
- Randomness: không dùng random để quyết định nghiệp vụ; trace event ID dùng `secrets`.
- Chạy: `day09 validate-inputs`, `day09 run`, `day09 validate`, `day09 package`.
- Không lưu API key, prompt bí mật hoặc dữ liệu ngoài case vào output/trace/submission.
