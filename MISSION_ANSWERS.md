# Day 14 Lab - Mission Answers

Môi trường chạy: MacBook Apple M4, Python 3.12, Docker 28.3.2. Mọi output dưới đây lấy từ lệnh đã chạy trên máy này ngày 26/11/2026; token và API key đã che.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Trong `01-localhost-vs-production/develop/app.py`:

1. **Secret ghi cứng trong code** (dòng 17–18): `OPENAI_API_KEY = "sk-hardcoded-…"` và `DATABASE_URL` chứa `admin:password123`. Push lên GitHub là lộ key, và đổi key thì phải sửa code rồi deploy lại.
2. **Log lộ secret** (dòng 34): `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` — key nằm trong log, ai đọc được log là lấy được key. Tôi chạy thử và thấy đúng dòng này trong output của server.
3. **Dùng `print()` thay cho logging có cấu trúc** (dòng 33, 38): không có level, timestamp hay field, không lọc/tổng hợp được trong log aggregator; còn in nguyên câu hỏi của người dùng (có thể chứa dữ liệu cá nhân).
4. **Không có health check** (dòng 42–43): gọi `GET /health` trả **404**, nên platform không biết agent còn sống hay đã treo để restart.
5. **Host và port cố định** (dòng 51–52): `host="localhost"` chỉ nhận kết nối từ chính máy đó, chạy trong container sẽ không ai gọi tới được; `port=8000` bỏ qua biến `PORT` mà Railway/Render cấp.
6. **Bật `reload=True` và `DEBUG = True` cứng** (dòng 21, 53): chế độ dev chạy luôn ở production, tốn tài nguyên theo dõi file và lộ thông tin debug.
7. **Không xử lý tắt an toàn**: không có lifespan hay xử lý SIGTERM, khi platform tắt container thì request đang chạy có thể bị cắt.
8. **Config rải rác trong code** (`MAX_TOKENS = 500`): muốn đổi giữa môi trường phải sửa code.

### Exercise 1.2: Chạy bản develop

```bash
$ curl -X POST "http://localhost:8000/ask?question=Hello"
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}

$ curl -o /dev/null -w '%{http_code}\n' http://localhost:8000/health
404
```

Log server cùng lúc (key đã che):

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-…
[DEBUG] Response: Đây là câu trả lời từ AI agent (mock). ...
```

Nó chạy, nhưng chưa sẵn sàng cho production: lộ key trong log, không có health check, chỉ chạy được trên localhost.

### Exercise 1.3: Comparison table

Chạy bản production (`cp .env.example .env && python app.py`):

```bash
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T08:44:36.238687+00:00"}
$ curl http://localhost:8000/ready
{"ready":true}
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","model":"gpt-4o-mini"}
```

Gửi `kill -TERM` → log `Waiting for application shutdown.` → `Application shutdown complete.` → `Finished server process`.

| Feature | Develop | Production | Why Important? |
|---------|---------|------------|----------------|
| Config | Ghi cứng trong code (`OPENAI_API_KEY`, `DEBUG`, `MAX_TOKENS`) | `config.py` đọc mọi giá trị qua `os.getenv` (dataclass `Settings`), có `validate()` | Cùng một image chạy được ở dev/staging/prod chỉ bằng cách đổi biến môi trường; đổi key không cần sửa code; secret không nằm trong git. |
| Secrets | Key thật nằm trong code và bị in ra log | Đọc từ env, log chỉ ghi `question_length`, không ghi key | Một lần lộ key là bị người khác dùng tiền của mình; log thường được nhiều người và nhiều hệ thống đọc. |
| Health check | Không có (`/health` → 404) | `/health` (liveness: uptime, version) và `/ready` (readiness: 503 khi chưa sẵn sàng) | Platform dùng `/health` để restart container treo, load balancer dùng `/ready` để ngừng gửi traffic tới instance chưa sẵn sàng mà không restart nó. |
| Logging | `print()` text tự do | Format JSON (`{"time":..,"level":..,"msg":..}`), log theo event | Log JSON lọc, tổng hợp và nối với trace được trong Loki/Datadog; có level để bật tắt DEBUG ở production. |
| Shutdown | Tắt đột ngột | Lifespan: đặt `is_ready = False`, chờ request đang chạy, rồi mới thoát | Khi deploy bản mới hoặc scale down, request của người dùng không bị cắt giữa chừng. |
| Network binding | `localhost:8000` cố định | `0.0.0.0` và `PORT` từ env | Container nhận được kết nối từ bên ngoài; chạy được trên Railway/Render nơi platform tự cấp port. |
| Reload/debug | `reload=True` luôn bật | Chỉ bật khi `DEBUG=true` | Không tốn tài nguyên và không lộ thông tin debug ở production. |

**Quan sát thêm:** khi chạy bản production, log trong `app.py` vẫn ra dạng text mặc định chứ không phải JSON. Lý do: `config.py` gọi `logging.warning("OPENAI_API_KEY not set …")` ngay lúc import, việc này tự cấu hình root logger, nên `logging.basicConfig(format=JSON)` chạy sau trong `app.py` không còn tác dụng. Đây là lý do ở Part 6 tôi gắn handler và `JsonFormatter` riêng cho logger của app thay vì dựa vào `basicConfig`.

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions

Trong `02-docker/develop/Dockerfile`:

1. **Base image:** `python:3.11` — bản Python đầy đủ (kèm compiler, header, công cụ build), nên image rất lớn.
2. **Working directory:** `/app`. Mọi lệnh `COPY`, `RUN`, `CMD` sau đó chạy tương đối với thư mục này.
3. **Vì sao `COPY requirements.txt` trước:** Docker cache theo từng layer và chỉ build lại từ layer đầu tiên bị thay đổi. Copy `requirements.txt` rồi `pip install` trước, sau đó mới copy code: khi chỉ sửa code thì layer cài dependency vẫn dùng cache, build mất vài giây thay vì cài lại toàn bộ thư viện. Nếu `COPY . .` trước thì mỗi lần sửa một dòng code là cài lại hết.
4. **`CMD` vs `ENTRYPOINT`:** `ENTRYPOINT` cố định chương trình sẽ chạy; tham số truyền vào `docker run` được nối thêm vào sau nó. `CMD` là lệnh hoặc tham số mặc định và bị thay thế hoàn toàn khi `docker run` truyền lệnh khác. Thường dùng `ENTRYPOINT` cho file chạy chính và `CMD` cho tham số mặc định. Viết ở dạng exec (`["python", "app.py"]`) để process là PID 1 và nhận được SIGTERM.

### Exercise 2.2: Build và run

```bash
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -p 8000:8000 my-agent:develop
$ curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":1.1,"container":true}
```

### Exercise 2.3: Image size comparison

Build cả hai từ gốc repo:

```bash
$ docker images my-agent
my-agent:advanced 265MB
my-agent:develop 1.67GB
```

- Develop: **1670 MB** (`docker images`; `docker image inspect` báo 415.4 MB dữ liệu nén)
- Production: **265 MB** (`docker image inspect` báo 57.5 MB dữ liệu nén)
- Difference: **giảm 84.1 %** ((1670 − 265) / 1670); tính theo số nén là giảm 86.2 %.

Multi-stage giảm dung lượng ở đâu:

- **Stage 1 (builder)** dùng `python:3.11-slim`, cài thêm `gcc`, `libpq-dev` và `pip install --user` toàn bộ dependency. Stage này không được dùng để chạy.
- **Stage 2 (runtime)** bắt đầu lại từ `python:3.11-slim` sạch, chỉ `COPY --from=builder /root/.local` (các package đã cài) cùng code. Compiler, header, cache apt và pip ở stage 1 bị bỏ lại.
- Image nhỏ hơn vì: base slim thay cho bản full (~1 GB), không mang theo công cụ build, và ít package hơn nên ít lỗ hổng bảo mật hơn. Thêm vào đó stage 2 chạy bằng user `appuser` (non-root) và có `HEALTHCHECK`.

### Exercise 2.4: Docker Compose stack

```text
              Internet
                 │ :80
          ┌──────▼──────┐
          │    nginx    │  reverse proxy / load balancer
          └──────┬──────┘  upstream agent_backend → agent:8000
                 │ (mạng nội bộ "internal")
          ┌──────▼──────┐
          │   agent     │  FastAPI, build từ 02-docker/production/Dockerfile
          └──┬───────┬──┘  không publish port ra ngoài
             │       │
   redis:6379│       │qdrant:6333
      ┌──────▼──┐ ┌──▼───────┐
      │  redis  │ │  qdrant  │
      │ session,│ │ vector DB│
      │ rate lim│ │ cho RAG  │
      └─────────┘ └──────────┘
```

Service được start: `redis`, `qdrant` (cả hai có healthcheck), sau đó `agent` (`depends_on` với `condition: service_healthy`), cuối cùng `nginx`. Chúng gọi nhau qua DNS nội bộ của Docker theo tên service (`redis://redis:6379/0`, `http://qdrant:6333`, `agent:8000`) trên network `internal`. Chỉ `nginx` mở port ra máy host (80/443).

```bash
$ docker compose -p k5-day14-docker up -d --build
agent  Up (health: starting)  8000/tcp
nginx  Up  0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant Up (healthy)  6333-6334/tcp
redis  Up (healthy)  6379/tcp

$ curl http://localhost/health
{"status":"ok","uptime_seconds":1.6,"version":"2.0.0","timestamp":"2026-11-26T08:48:17.102773"}
$ curl -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

---

## Part 3: Cloud Deployment

Theo hướng dẫn lớp K5, tôi deploy luôn agent Part 6 (`06-lab-complete/`) thay vì app mẫu.

### Exercise 3.2: Render deployment (nền tảng chính)

- **URL:** https://production-ai-agent-kp4j.onrender.com
- **Cách deploy:** Render Blueprint đọc `06-lab-complete/render.yaml`, tạo 2 tài nguyên gói free: web service `production-ai-agent-kp4j` (Docker, `rootDir: 06-lab-complete`, health check `/health`) và Key Value (Redis) `agent-redis-kp4j`. `AGENT_API_KEY` dùng `generateValue: true` (Render tự sinh), `REDIS_URL` lấy bằng `fromService` từ Redis. Không có secret nào trong code.
- **Tự kiểm (CP6):** `/health` → 200, `/ready` → 200, `/ask` không có key → 401, `/ask` có key (key lấy từ Render Dashboard, không ghi vào repo) → 200.
- Chi tiết lệnh và biến môi trường: [DEPLOYMENT.md](DEPLOYMENT.md). Ảnh: [dashboard](screenshots/dashboard.png), [running](screenshots/running.png), [test](screenshots/test.png).

### Nền tảng còn lại: so sánh `railway.toml` với `render.yaml`

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Phạm vi | Chỉ cấu hình build/deploy của **một** service | Blueprint mô tả **cả hạ tầng**: web service + Redis, và nối chúng với nhau | Render coi file là Infrastructure-as-Code cho cả project; Railway tạo Redis và các service khác qua dashboard/CLI. |
| Build | `[build] builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `dockerfilePath`, `rootDir` | Render cần `rootDir` vì Blueprint đọc từ gốc repo còn code nằm trong thư mục con; Railway deploy thư mục đang đứng (`railway up` trong `06-lab-complete/`). |
| Health check | `[deploy] healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` | Cùng ý nghĩa, khác cú pháp TOML/YAML. |
| Restart | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries` | Render tự restart, không cấu hình trong file | Railway cho chỉnh chính sách restart theo từng service. |
| Biến môi trường | Không ghi trong file; đặt bằng `railway variables set` hoặc dashboard | Khai báo ngay trong file: giá trị thường ghi thẳng, secret dùng `generateValue: true` hoặc `sync: false`, `REDIS_URL` lấy bằng `fromService` | Blueprint muốn tái tạo được cả môi trường từ git nhưng không được lộ secret, nên có cơ chế sinh/nhập secret riêng. |
| Port | Railway cấp `PORT`, `CMD` đọc `${PORT}` | Render cũng cấp `PORT` | Cả hai đều cần app đọc `PORT`, nên `CMD` dùng `${PORT:-8000}`. |

### Exercise 3.3: Cloud Run CI/CD pipeline

`cloudbuild.yaml` chạy 4 bước nối tiếp nhau (`waitFor`): (1) **test** — cài `requirements.txt` và chạy `pytest`, fail thì dừng pipeline; (2) **build** — build image gắn hai tag `$COMMIT_SHA` và `latest`, dùng `--cache-from` image `latest` để tận dụng layer cache; (3) **push** — đẩy cả hai tag lên Container Registry; (4) **deploy** — `gcloud run deploy` image theo tag commit (rollback được bằng tag cũ), region `asia-southeast1`, `min-instances=1` để tránh cold start, `max-instances=10`, secret `OPENAI_API_KEY` lấy từ Secret Manager. `service.yaml` mô tả cùng service theo kiểu Knative khai báo: tự scale 1–10 instance với 80 request đồng thời mỗi instance, timeout 60 giây, giới hạn CPU/RAM, secret qua `secretKeyRef`, liveness probe `/health` và startup probe `/ready`.

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop`)

- **Kiểm key ở đâu:** hàm `verify_api_key` dùng `APIKeyHeader(name="X-API-Key")`, gắn vào `/ask` qua `Depends(verify_api_key)`. Key đúng đọc từ biến `AGENT_API_KEY`.
- **Sai key:** thiếu header → **401** "Missing API key"; có header nhưng sai → **403** "Invalid API key".
- **Rotate key:** đổi biến `AGENT_API_KEY` trên platform rồi restart, không cần sửa code. Để không làm gián đoạn client, nên cho phép tạm thời hai key cùng hợp lệ (key cũ và mới), báo client chuyển sang key mới, rồi gỡ key cũ.

```bash
# Không có key
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

# Sai key
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

# Đúng key (AGENT_API_KEY=secret-key-123)
HTTP/1.1 200 OK
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production`)

Luồng JWT: client gửi username/password tới `POST /auth/token` → server kiểm tra rồi ký token HS256 chứa `sub`, `role`, `exp` (hết hạn sau 60 phút) → client gửi `Authorization: Bearer <token>` → `verify_token` kiểm chữ ký và hạn mà không cần tra database.

```bash
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" \
    -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
{"access_token":"<đã che>","token_type":"bearer","expires_in_minutes":60,...}

# Không có token
HTTP/1.1 401 Unauthorized
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

# Token giả
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

# Token hợp lệ
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Tôi là AI agent được deploy lên cloud. ...","usage":{"requests_remaining":9,"budget_remaining_usd":1.9e-05}}
```

### Exercise 4.3: Rate limiting

- **Thuật toán:** sliding window log — mỗi user một `deque` timestamp; mỗi request bỏ các timestamp cũ hơn 60 giây, đếm phần còn lại, vượt giới hạn thì trả 429.
- **Giới hạn:** user thường 10 request / 60 giây (`rate_limiter_user`); admin 100 request / 60 giây (`rate_limiter_admin`).
- **Bypass cho admin:** không bỏ hẳn giới hạn mà chọn limiter theo `role` đọc từ JWT (`limiter = rate_limiter_admin if role == "admin" else rate_limiter_user`), nên admin có trần cao hơn.

Vòng lặp 20 request bằng token `student` (đã dùng 1 request ở bước 4.2):

```text
req  1: 200 ... (requests_remaining giảm dần)
...
req  9: 200
req 10: 429 {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":50}}
...
req 20: 429

HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
retry-after: 50
```

Request thứ 10 trong vòng lặp là request thứ 11 trong phút đó, nên bị chặn đúng ở giới hạn 10.

**Hạn chế của bản mẫu:** limiter lưu trong RAM của process, nên chạy nhiều instance hoặc restart là mỗi bên đếm riêng và giới hạn mất tác dụng. Ở Part 6 tôi chuyển sang sorted set trong Redis (`ZREMRANGEBYSCORE → ZCARD → ZADD → EXPIRE`); script chấm kiểm tra được rằng restart agent xong vẫn bị 429.

### Exercise 4.4: Cost guard implementation

**Rate limit bảo vệ tốc độ, cost guard bảo vệ tiền.** Một user gọi đúng 10 request/phút nhưng mỗi câu hỏi rất dài vẫn có thể đốt hết ngân sách, nên cần cả hai.

Cách tôi làm trong `06-lab-complete/app/cost_guard.py`:

1. **Kiểm trước khi gọi LLM** (`check_budget`): đọc tổng chi phí ngày và tháng trong Redis bằng một lệnh `MGET`. Đã vượt `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD` thì trả **402 Payment Required** ngay, không tốn token.
2. **Ghi sau khi gọi** (`record_cost`): tính chi phí từ token (`input/1000 × $0.00015 + output/1000 × $0.0006`), cộng vào hai key `cost:daily:YYYY-MM-DD` và `cost:monthly:YYYY-MM` bằng `INCRBYFLOAT` trong một pipeline.
3. **Tự reset:** key mang ngày/tháng UTC, sang ngày/tháng mới là key mới; `EXPIRE` 2 ngày và 32 ngày để Redis tự dọn.
4. **Lưu trong Redis** nên mọi worker và instance dùng chung một tổng, restart không mất.
5. **Redis lỗi thì trả 503** thay vì cho qua, vì không kiểm được ngân sách thì không nên tiếp tục tiêu tiền.

Kết quả từ script chấm: đặt `DAILY_BUDGET_USD=0.000001` thì request thứ nhất được phục vụ (chi phí ghi vào Redis), request kế tiếp trả **402**.

So với lời giải mẫu trong đề (`current + estimated_cost > 10` rồi cộng luôn): cách đó cộng chi phí *ước tính* trước khi gọi, còn tôi cộng chi phí *thực tế* sau khi có token. Đổi lại, cách của tôi có thể vượt ngân sách tối đa bằng chi phí của các request đang chạy song song cùng lúc.

**Quan sát ở bản mẫu Part 4:** trường `budget_remaining_usd` trong response thực ra trả về `usage.total_cost_usd` — tức số tiền **đã dùng**, không phải số còn lại (thấy ở output 4.2: `1.9e-05` sau một request).

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks

`05-scaling-reliability/develop/app.py` có:

- `GET /health` (liveness): trả 200 kèm uptime, version, kiểm memory. Chỉ trả lời câu hỏi "process còn sống không?". Fail thì platform **restart** container.
- `GET /ready` (readiness): trả 503 khi `_is_ready = False` (đang khởi động hoặc đang tắt), kèm số request đang xử lý. Fail thì load balancer **ngừng gửi traffic** nhưng không restart.

```bash
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.7,"version":"1.0.0","environment":"development",...,"checks":{"memory":{"status":"ok","used_percent":80.7}}}
$ curl http://localhost:8000/ready
{"ready":true,"in_flight_requests":1}
```

(`in_flight_requests` = 1 vì middleware đếm luôn chính request `/ready`.)

Vì sao phải tách: nếu Redis chết mà dùng readiness làm liveness, platform sẽ restart agent liên tục mà không giải quyết được gì. Ở Part 6, `/health` luôn 200 còn `/ready` trả 503 khi mất Redis — script chấm kiểm tra cả hai.

### Exercise 5.2: Graceful shutdown

Lifespan của app: khi shutdown đặt `_is_ready = False` (để `/ready` trả 503, load balancer ngừng gửi request mới), rồi chờ tới khi `_in_flight_requests == 0` (tối đa 30 giây) mới thoát. uvicorn chạy với `timeout_graceful_shutdown=30`.

Test: gửi `/ask` rồi `kill -TERM` ngay sau 50 ms.

```text
process exit code: 0
request result: {"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."} [200]

INFO:     Waiting for application shutdown.
INFO 🔄 Graceful shutdown initiated...
INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process
```

Request đang chạy vẫn hoàn thành với 200, process thoát mã 0.

Lưu ý: file đăng ký `signal.signal(SIGTERM, handle_sigterm)` lúc import, nhưng uvicorn tự cài handler SIGTERM của nó khi chạy, nên handler tự viết chỉ chạy lúc cuối (dòng `Received signal 15` in sau `Finished server process`). Nếu handler tự viết chặn mất handler của uvicorn thì process sẽ không tắt. Vì vậy ở Part 6 tôi không đăng ký signal handler riêng mà để uvicorn xử lý SIGTERM và chỉ dọn dẹp trong lifespan.

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` lưu session và lịch sử hội thoại trong Redis (`session:<id>` với `SETEX` TTL 1 giờ), không trong dict của process. Lý do: khi chạy nhiều instance sau load balancer, mỗi instance có RAM riêng; request 1 vào instance A tạo session, request 2 vào instance B sẽ không thấy session đó. Đưa state ra Redis thì instance nào cũng đọc được, nên scale ngang và restart thoải mái. Part 6 làm tương tự cho state của rate limit và cost guard.

### Exercise 5.4: Load balancing

```bash
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
k5-day14-scaling-agent-1 Up (healthy)
k5-day14-scaling-agent-2 Up (healthy)
k5-day14-scaling-agent-3 Up (healthy)
k5-day14-scaling-nginx-1 Up
k5-day14-scaling-redis-1 Up (healthy)
```

10 request `POST http://localhost:8080/chat` qua Nginx, đếm theo `served_by`:

```text
   4 served_by instance-483369
   3 served_by instance-95a810
   3 served_by instance-e44abb
```

Nginx chia round-robin gần đều cho 3 instance.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
...
Total requests: 5
Instances used: {'instance-483369', 'instance-e44abb', 'instance-95a810'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

5 câu hỏi trong cùng một session lần lượt rơi vào cả 3 instance, nhưng lịch sử đọc lại vẫn đủ 10 message (5 câu hỏi + 5 trả lời), vì mọi instance đọc và ghi cùng một key trong Redis.

Script không tự tắt instance nào, nên tôi thử thêm: `docker stop k5-day14-scaling-agent-1` rồi gửi 6 request. Cả 6 đều trả 200, luân phiên giữa 2 instance còn lại — Nginx tự bỏ instance chết và session không mất vì nằm trong Redis.

---

## Part 6: Final Project — output `check_production_ready.py`

Chạy đầy đủ (có Docker) trong `06-lab-complete/`, kèm kiểm tra URL đã deploy:

```text
✅ Docker: image < 500 MB — 63 MB
✅ Public URL: GET https://production-ai-agent-kp4j.onrender.com/health → 200

  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

---

## AI log

Tôi dùng Claude Code (Anthropic) làm trợ lý trong suốt lab. Bảng dưới ghi việc AI đã làm và cách kiểm tra.

| Phần | Tôi hỏi / giao việc | AI làm gì | Cách kiểm tra và chỉnh sửa |
|---|---|---|---|
| Tổng quan | Lab làm gì, cần lưu ý gì | Đọc README, RULES, CHECKPOINTS, RUBRIC, tóm tắt các mốc và bẫy kỹ thuật | Đối chiếu lại với file đề trong repo |
| Part 6 code | Hoàn thành các TODO trong `06-lab-complete/` | Viết `config.py`, `auth.py`, `redis_client.py`, `rate_limiter.py`, `cost_guard.py`, `main.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Chạy `check_production_ready.py` bản đầy đủ → PASS 12/12; tự test thêm trên `docker compose`: thiếu key 401, body JSON hỏng 422 không lộ stack trace, vượt giới hạn 429 kèm `Retry-After` |
| Part 3 deploy | Deploy lên Render | Tạo Render Blueprint qua Render MCP (sau khi tôi duyệt kế hoạch), theo dõi tới khi `/ready` trả 200, viết `DEPLOYMENT.md` | Chạy 4 lệnh tự kiểm CP6 trên URL public; `check_production_ready.py --public-url` |
| Part 7 benchmark | Chạy load test, eval, sửa knowledge base, CI gate | Chạy `load_test.py`, `run_eval.py`, thêm metric `agent_question_length_chars`, 2 entry knowledge base (HPA, JWT), workflow `eval-quality-gate.yml` | Eval cuối 23/23 không regression; mọi số trong `BENCHMARK_REPORT.md` lấy từ file JSON/CSV đã nộp |
| Part 7 debug | Stack observability trả 502 | Đọc log container, tìm ra image agent của đề thiếu `PYTHONUSERBASE` nên không import được `uvicorn`; phát hiện `docker compose run locust` bật lại 2 agent nên phải thêm `--no-deps` | Rebuild, agent healthy, chạy lại benchmark và xác nhận đúng 1 rồi 3 instance |
| Part 7 alerts, tracing | Làm thí nghiệm A và C, lấy trace | Đổi `.env`, đo thời gian tới firing qua Prometheus API, lấy trace chậm và trace lỗi qua Jaeger API, grep log theo `trace_id` | Hai alert cùng firing trên trang Alerts; trace_id trong log khớp với trace trong Jaeger |
| Ảnh chụp Part 7 | Chụp 4 ảnh | Dùng Playwright chụp Prometheus, Grafana, Jaeger | Mở từng ảnh kiểm tra nội dung; ảnh Grafana chụp lại nhiều lần vì chụp cả màn hình bị trống panel |
| Part 1–5 | Chạy các bài và viết câu trả lời | Chạy app develop/production, build image, chạy compose, test auth/rate limit/shutdown/stateless, soạn file này | Mọi output trong file là output thật của các lệnh đã chạy; số MB đo bằng `docker images` |

Không có con số nào trong bài được ước lượng hay do AI tự đặt ra; khi một lần đo bị lỗi (Locust 100 % lỗi 502), tôi bỏ kết quả đó, sửa nguyên nhân rồi đo lại.
