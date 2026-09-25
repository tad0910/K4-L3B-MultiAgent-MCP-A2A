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
| Entity/customer | candidate_order_ids, customer_unique_id_hint | Phân giải thực thể đơn hàng, loại bỏ candidate giả, lấy lịch sử khách hàng | get_order, get_customer_history | resolved_order_ids, rejected_candidates, related_order_ids |
| Coordinator | TODO | TODO | TODO | TODO |
| Order/product | TODO | TODO | TODO | TODO |
| Shipment | TODO | TODO | TODO | TODO |
| Payment/refund | TODO | TODO | TODO | TODO |
| Policy | TODO | TODO | TODO | TODO |
| Conflict resolver | TODO | TODO | TODO | TODO |
| Verifier | TODO | TODO | TODO | TODO |

Áp dụng least privilege; tool discovery không đồng nghĩa mọi actor đều được gọi mọi tool.

## 3. Entity resolution và A2A protocol

- **Chiến lược lọc và xếp hạng ứng viên (Candidate Ranking & Rejection)**:
  - *Bộ lọc cú pháp (Pre-filter)*: Kiểm tra định dạng Regex hex 32 ký tự (`^[0-9a-fA-F]{32}$`). Các mã giả lập, synthetic candidates (như `candidate-001`, `candidate-002`) hoặc chuỗi không hợp lệ sẽ bị đưa thẳng vào `rejected_candidates` mà không tốn lượt gọi MCP, bảo toàn tuyệt đối Call Budget.
  - *Thứ tự ưu tiên (Ranking priority)*: Mã `claimed_order_id` do khách hàng khiếu nại (nếu nằm trong candidate list) được xếp ưu tiên số 1 để xác thực trước.
  - *Xác thực thực thể (MCP Verification)*: Gọi `get_order` qua MCP Gateway với `order_id` để kiểm tra đơn hàng có tồn tại thật trong cơ sở dữ liệu Olist.
  - *Ngưỡng tự tin (Confidence Threshold)*:
    - `0.98`: Đơn khiếu nại `claimed_order_id` tồn tại và trùng khớp hoàn toàn trong hệ thống.
    - `0.90`: Tìm thấy đơn hàng thực tế nhưng khác với mã khách hàng tự điền.
    - `0.10`: Toàn bộ candidate đều bị reject / không tìm thấy đơn (status: `not_found`).
    - `0.50`: Tồn tại nhiều đơn hợp lệ mà không đủ dữ liệu phân biệt (status: `ambiguous`).

- **A2A Message Envelope & Luồng giao tiếp**:
  - *AgentContext*: Toàn bộ trạng thái điều tra được đóng gói trong đối tượng `AgentContext` bao gồm: `case_id`, `gateway`, `trace`, `findings` và danh sách `evidence_refs`.
  - *Correlation theo `case_id`*: Mọi sự kiện phát sinh, cuộc gọi MCP và trace event đều gắn nhãn `case_id` tương ứng, ngăn ngừa tuyệt đối việc rò rỉ dữ liệu chéo giữa các case.
  - *Điều kiện Handoff & Chống lặp (Anti-loop)*: Luồng điều phối là đồ thị có hướng không chu trình (DAG tuyến tính): `Coordinator ➔ Entity Resolver ➔ Customer Context ➔ Shipment ➔ Payment ➔ Conflict & Root-cause ➔ Verifier`. Mỗi specialist agent chỉ chạy đúng 1 lần theo thứ tự, loại bỏ hoàn toàn nguy cơ lặp vô hạn (infinite loop).

## 4. Evidence và conflict lifecycle

- **Kiểm định phản hồi MCP (Response Validation)**:
  - Mọi phản hồi từ Gateway đều được kiểm tra JSON Schema theo hợp đồng `day09-mcp-evidence-v1`. Nếu schema sai hoặc lỗi server, agent xử lý ngoại lệ graceful và không đoán mò dữ liệu.
- **Vòng đời bằng chứng (Evidence Provenance Lifecycle)**:
  - *Thu thập*: Chỉ chấp nhận `evidence_ref` hợp lệ từ server MCP trả về; tuyệt đối không sinh giả lập hoặc chỉnh sửa chuỗi `ev_*`.
  - *Ghi nhận tiêu thụ (Consumption Trace)*: Mỗi khi một Agent sử dụng dữ liệu từ MCP, sự kiện `tool_result_consumed` được phát ra ngay lập tức với `actor`, `tool_name` và danh sách `evidence_refs` tương ứng.
  - *Phạm vi độc lập (Case Isolation)*: Bằng chứng được lưu theo bộ nhớ đệm `context.cache` và danh sách `context.evidence_refs` riêng biệt của từng case. Sau khi hoàn thành một case, toàn bộ cache được khởi tạo lại, đảm bảo không có bằng chứng nào được dùng chéo case.
- **Biểu diễn và ánh xạ Claim-to-Evidence**:
  - Mọi đánh giá khiếu nại (`claim_assessments`) đều được liên kết trực tiếp với tối đa 5 `evidence_refs` liên quan nhất từ MCP.
  - Khi có sự mâu thuẫn giữa lời khai khiếu nại và mốc thời gian ghi nhận từ hệ thống, agent ưu tiên nguồn dữ liệu authoritative từ cổng vận chuyển và ngân hàng.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event/code |
| --- | ---: | --- | --- |
| MCP timeout | TODO | TODO | TODO |
| Entity not found/ambiguous | TODO | TODO | TODO |
| Source conflict | TODO | TODO | TODO |
| Invalid specialist result | TODO | TODO | TODO |

Nêu query budget/cache strategy để tránh gọi lặp và quét rộng. Retry phải có giới hạn, idempotent và không biến missing evidence thành dữ liệu phỏng đoán.

## 6. Verification invariants

Liệt kê kiểm tra trước finalize: schema, entity scope, rejected candidates, evidence ownership, claim linkage, timeline, payment/refund totals, source precedence, responsibility/action consistency và confidence bounds.

## 7. Reproducibility

Ghi model/config, dependency pinning, concurrency limit, random seed (nếu có), lệnh chạy và giới hạn tài nguyên. Không ghi API key.
