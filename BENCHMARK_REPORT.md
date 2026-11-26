# Benchmark & Observability Report — Day 14

**Họ tên:** Nguyễn Văn Hải Quân
**Ngày chạy:** 26/11/2026
**Môi trường:** MacBook Apple M4, 16 GB RAM; Docker Desktop 28.3.2 (10 CPU / 7.7 GB cấp cho Docker); Python 3.12

Số liệu lấy từ: `reports/*.json` (load test, eval), `benchmark-results/scale_*_stats.csv` (Locust), Prometheus / Jaeger API của stack `production/`.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Mock LLM mất ~190 ms ở p50; 500 ms cho dư ~2.5 lần cho retrieval, mạng và tải cao mà người dùng chat vẫn thấy nhanh. Đặt theo p95 vì average che tail latency (mục 2.2). |
| Error rate `/ask` | < 2 % | Upstream LLM có ~1 % lỗi nền (`MOCK_ERROR_RATE=0.01`); 2 % là gấp đôi mức nền, đủ chặt để bắt sự cố thật mà không báo động vì dao động. Alert đặt lỏng hơn (5 % trong 1 phút) để tránh alert fatigue. |
| Accuracy trên golden dataset | ≥ 85 % (gate CI), mục tiêu 100 % cho release | 23 câu là mẫu nhỏ, mỗi câu ≈ 4.3 điểm %; 85 % cho phép sai tối đa 3 câu. Kèm điều kiện không có regression so với baseline. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2, 300 request mỗi mức)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.70 | 190.4 | 217.4 | 1673.2 | 1.00 % | ✅ PASS |
| 10 | 36.96 | 188.3 | 218.9 | 1698.3 | 1.33 % | ✅ PASS |
| 50 | 115.96 | 192.0 | 222.2 | 1695.8 | 1.67 % | ✅ PASS |
| 100 | 59.37 | 511.3 | 3627.9 | 3852.7 | 1.67 % | ❌ FAIL (p95) |

**Nhận xét:** RPS gần tuyến tính từ 1 → 10 (×7.9) vì mỗi request chủ yếu là chờ I/O (`asyncio.sleep` mô phỏng LLM), nên event loop phục vụ song song mà p50 gần như không đổi (~190 ms). Từ 10 → 50 chỉ còn ×3.1: bắt đầu có overhead xử lý. Ở 100, RPS **giảm** còn 59 và p50 tăng lên 511 ms, p95 lên 3.6 s: agent chạy 1 process Python, phần việc CPU (parse JSON, Pydantic, ghi log, cập nhật metrics) dồn vào một event loop nên request phải xếp hàng. Đây là giả thuyết dựa trên hình dạng số liệu (latency tăng mà error rate không đổi = quá tải hàng đợi, không phải lỗi), chưa đo CPU để xác nhận. Kết luận: một instance chịu được ~50 request song song trong SLO; muốn hơn thì scale ngang thay vì tăng concurrency trên một process.

### 2.2 Tail latency (concurrency 20, 300 request)

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 188.0 | 215.7 | 220.5 |
| 0.03 (mặc định) | 213.7 | 219.5 | 1675.8 |
| 0.1 | 294.5 | 1669.0 | 1698.2 |

**Nhận xét:** Nhạy nhất là percentile đuôi. Với 3 % request chậm, p99 nhảy từ 220 ms lên 1676 ms (×7.6) trong khi avg chỉ tăng 14 % và p95 gần như đứng yên (vì 3 % < 5 %). Khi tỉ lệ chậm lên 10 %, nó vượt qua ngưỡng 5 % nên p95 cũng nhảy lên 1669 ms, còn avg vẫn chỉ 294 ms. Average trộn 97 request nhanh với 3 request rất chậm nên trông "ổn", trong khi cứ 100 người thì vài người chờ gần 2 giây. SLO đặt theo p95/p99 mới phản ánh trải nghiệm của những người dùng tệ nhất.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | 100 % (23/23) |
| Avg tokens / câu | 57.3 | 108.7 | 115.2 |
| Tổng cost (USD) | 0.000408 | 0.000756 | 0.000804 |
| Cost / câu đúng (USD) | 0.00040755 | 0.00003599 | 0.00003495 |
| Quality gate | FAIL (ngưỡng 80 %) | PASS (ngưỡng 80 %, 0 regression) | PASS (ngưỡng 100 %, 0 regression) |

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` (JWT khác gì API key) | `token`, `hết hạn`, `chữ ký` | Retrieval không tìm được tài liệu nào chứa "jwt" nên LLM trả lời chung chung. Thêm entry `(["jwt"], ...)` vào `KNOWLEDGE_BASE` mô tả JWT là token có chữ ký số, chứa claims và thời điểm hết hạn, so với API key là chuỗi cố định. |
| `scaling-03` (Kubernetes HPA scale theo chỉ số nào) | `cpu`, `memory`, `custom metrics` | Thêm entry `(["hpa", "autoscal"], ...)` mô tả HPA tăng giảm pod theo CPU, memory hoặc custom metrics. Chọn keyword hẹp để không khớp nhầm câu hỏi khác → eval lại không có regression. |

Sửa ở `develop/utils/mock_llm.py` và chép sang `production/utils/mock_llm.py` để stack Docker dùng cùng knowledge base. Không sửa `golden_dataset.jsonl`.

**Quality gate trong CI:** `run_eval.py` trả exit code 1 khi accuracy dưới `--min-accuracy` hoặc có regression so với `--baseline`. Chạy eval v1 với `--baseline reports/eval_v2.json` cho exit code **1**. File `.github/workflows/eval-quality-gate.yml` chạy agent v2 rồi `run_eval.py --min-accuracy 0.85` trên mọi PR đụng tới `benchmark-observability/`; step fail → job fail → bật branch protection "require status checks" thì PR không merge được.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:** v2 thêm retrieval nên prompt dài gấp đôi (57 → 109 token/câu), tổng cost tăng 85 % và p95 eval tăng ~31 ms (181 → 212 ms). Đổi lại accuracy tăng từ 4.3 % lên 91.3 %, nên **cost / câu đúng giảm ~11 lần** (0.000408 → 0.000036 USD). So theo cost / câu thì v1 rẻ hơn, nhưng câu trả lời sai không có giá trị, nên chỉ số đúng để so là cost / câu đúng. Sửa KB tăng thêm 6.5 token/câu mà lấy được 2 câu, cost / câu đúng còn giảm tiếp.

---

## 4. Observability stack (production)

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals) — `screenshots/grafana-dashboard.png`
- [ ] Một trace chậm trong Jaeger — `screenshots/jaeger-slow-trace.png`
- [ ] Một trace lỗi trong Jaeger (có event `exception`) — `screenshots/jaeger-error-trace.png`
- [ ] Trang Alerts của Prometheus với 2 alert cùng **firing** — `screenshots/prometheus-alert-firing.png`

**Trả lời Exercise 7.4:**

1. *p95 và avg:* trong lần chạy Locust 100 user với 1 instance, `/ask` có avg 234.8 ms nhưng p95 230 ms và p99 1700 ms. Avg cao hơn p95 vì ~3 % request mất ~1.7 s kéo trung bình lên; khoảng cách giữa avg/p95 và p99 chính là tail latency do upstream LLM chậm bất thường.
2. *Chia tải:* có. Với 3 instance và tải 15 user, `rate(http_requests_total{route="/ask"}[2m])` theo instance là 2.70 / 2.68 / 2.76 req/s — Nginx round-robin chia gần đều.
3. *Input tokens/s > output tokens/s* (502 so với 265 token/s): agent v2 nhét tài liệu retrieval vào prompt, nên input gồm câu hỏi + context dài, còn output chỉ là câu trả lời ngắn.
4. `curl http://localhost:8080/metrics` trả **404**: Nginx chỉ proxy `/ask` và `/health`, chặn `/metrics` để không lộ số liệu nội bộ ra ngoài; Prometheus scrape thẳng từng agent qua mạng nội bộ Docker.

### 4.2 Thí nghiệm "phá hệ thống"

Tải nền: Locust headless, 3 instance agent. Thời gian đo bằng cách poll `GET /api/v1/alerts` của Prometheus mỗi 5 giây.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | 82 s (pending sau 21 s + `for: 1m`) | Error rate (Golden Signals) chuyển đỏ; req/s theo status thấy 503 tăng |
| B | `MOCK_SLOW_RATE=0.3` | Chưa làm | — | — |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | 151 s (pending sau 34 s + `for: 2m`) | Cost USD/giờ: 1.09 → 7.21 $/giờ lúc firing; LLM p95 tăng |
| D | `docker pause` 1 instance | Chưa làm | — | — |
| E | `AGENT_VERSION=v1` | Chưa làm | — | — |

Áp dụng A lúc 08:17:33 UTC (firing 08:18:55), giữ A rồi áp dụng C lúc 08:19:06 UTC (firing 08:21:37), nên từ 08:21:37 hai alert cùng firing.

**Phát hiện khi chuẩn bị thí nghiệm C:** với tải 30 user như đề gợi ý, chỉ riêng model nhỏ đã tiêu ~2.96 $/giờ (18.4 req/s × ~0.00004 $/request), vượt ngưỡng 2 $/giờ của `LLMCostBurnRateHigh`, nên alert này firing ngay ở baseline và không đo được thí nghiệm C. Tôi giảm tải nền xuống 15 user (baseline ~1.1–1.6 $/giờ) rồi mới làm C. Bài học: ngưỡng cost phải tính từ traffic dự kiến; một ngưỡng tuyệt đối đúng ở tải này lại thành báo động giả ở tải gấp đôi. Thực tế nên alert theo budget ngày hoặc theo cost/request thay vì cost/giờ tuyệt đối.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Lỗi chất lượng không làm metric hạ tầng đổi màu: v1 vẫn trả 200, còn nhanh và rẻ hơn. Cách bắt:
- **Eval gate trước khi deploy:** `run_eval.py --baseline` trong CI chặn ngay bản v1 (accuracy 4.3 %, exit code 1).
- **Online eval trên production:** lấy mẫu ~1 % request, chấm bằng LLM-as-judge hoặc so keyword như golden dataset, đẩy điểm thành một gauge Prometheus và alert khi điểm tụt.
- **Metric đại diện cho chất lượng:** retrieval hit ratio về 0, tokens/câu giảm đột ngột, bảng version đổi — alert khi các tín hiệu này lệch khỏi baseline.
- **Tín hiệu từ người dùng:** tỉ lệ thumbs-down, hỏi lại, regenerate.

### 4.3 Debug bằng trace

Trace chậm nhất trong 1 giờ (Jaeger API v3, service `observable-agent`):

- `trace_id`: `201fad30383b13828fdd8bae2b39175d`
- Tổng thời gian: 1751.6 ms
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (1728.0 ms, 98.7 %). `agent.retrieve_context` chỉ 21.4 ms (1.2 %). Kết luận: chậm do upstream LLM, không phải code của agent hay retrieval → hướng xử lý là timeout + retry / fallback model, không phải tối ưu code.
- Dòng log tương ứng (grep theo `trace_id`):

```json
{"ts": "2026-11-26T08:15:57.794230+00:00", "level": "INFO", "service": "observable-agent", "instance": "01609971c6d6", "event": "llm_call", "trace_id": "201fad30383b13828fdd8bae2b39175d", "span_id": "b83753e588cdc971", "request_id": "2f90f1fafecd", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 75, "output_tokens": 37, "cost_usd": 3.3e-05, "llm_ms": 1692.2}
{"ts": "2026-11-26T08:15:57.794431+00:00", "level": "INFO", "service": "observable-agent", "instance": "01609971c6d6", "event": "http_request", "trace_id": "201fad30383b13828fdd8bae2b39175d", "span_id": "b83753e588cdc971", "request_id": "2f90f1fafecd", "method": "POST", "route": "/ask", "status": 200, "ms": 1751.2}
```

Trace lỗi: `trace_id` `55c6d4b4e148f02849f7614db2a5c542`, 116.7 ms. Span `chat mock-llm-small` có status ERROR, `error.type=upstream_unavailable` và event `exception` (`utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`). Log khớp:

```json
{"ts": "2026-11-26T08:15:57.469567+00:00", "level": "INFO", "service": "observable-agent", "instance": "01609971c6d6", "event": "llm_error", "trace_id": "55c6d4b4e148f02849f7614db2a5c542", "span_id": "40b80e2002e9cec4", "request_id": "2f22b1d779b3", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, ramp 10/s, 2 phút)

Số liệu của dòng `POST /ask` trong `benchmark-results/scale_*_stats.csv`.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.97 | 190 | 230 | 1700 | 72 / 7156 (1.0 %) |
| 3 | 59.58 | 190 | 230 | 1700 | 75 / 7107 (1.1 %) |

**Giải thích kết quả:** Scale từ 1 lên 3 instance **không** cải thiện RPS hay p95. Nút thắt không nằm ở agent:
- Locust giữ cố định 100 user và mỗi user nghỉ giữa các request, nên tải tối đa bị giới hạn bởi số user (~60 req/s), không phải bởi năng lực server. Một instance đã xử lý hết mức tải này mà không quá tải (p50 vẫn 190 ms như khi chạy đơn lẻ).
- Latency do upstream LLM quyết định (~190 ms bình thường, ~1.7 s cho ~3 % request chậm); thêm instance không làm LLM nhanh hơn, nên p99 vẫn 1700 ms.
- Failures ~1 % ở cả hai là lỗi nền của mock LLM (`MOCK_ERROR_RATE=0.01`).

Muốn thấy lợi ích của scale ngang phải tăng tải tới mức 1 instance bão hòa (như c=100 ở mục 2.1, p95 vọt lên 3.6 s) — lúc đó chia ra 3 instance mới giảm được hàng đợi. Lưu ý thêm khi đo: lệnh `docker compose run locust` mặc định bật lại service phụ thuộc theo `replicas: 2`, nên phải thêm `--no-deps` để bài "1 instance" thật sự chỉ có 1 agent.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. Average nói dối: 3 % request chậm làm p99 tăng 7.6 lần trong khi avg chỉ tăng 14 %. SLO và alert phải đặt theo percentile.
  2. Benchmark phải kiểm chứng điều kiện đo trước khi tin số: lần chạy Locust đầu tiên cho 100 % lỗi 502 vì image agent hỏng (`PYTHONUSERBASE` sai), và benchmark "1 instance" thực ra có 2 agent nếu thiếu `--no-deps`. Đọc log và trace trước, ghi số sau.
  3. Metric hạ tầng không thấy lỗi chất lượng (thí nghiệm E) và ngưỡng alert phụ thuộc tải (cost alert firing sẵn ở 30 user). Cần cả eval (offline trong CI + online sampling) lẫn alert được hiệu chỉnh theo traffic thực.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - Gauge điểm chất lượng từ online eval (sample 1 %) + alert khi tụt quá 10 % so với baseline 7 ngày.
  - Alert cost theo **budget ngày** và **cost/request** thay cho cost/giờ tuyệt đối; tách theo `feature` và `model`.
  - Synthetic check từ bên ngoài + metric ở tầng Nginx (5xx, upstream timeout) để thấy lỗi 504 mà agent không đếm được.
  - Alert theo SLO burn rate (multi-window: 5 phút + 1 giờ) thay cho ngưỡng tĩnh, để vừa bắt nhanh sự cố lớn vừa tránh báo động vì spike ngắn.
  - Retrieval empty-result rate và tool-call success rate cho agent có RAG/tool.
