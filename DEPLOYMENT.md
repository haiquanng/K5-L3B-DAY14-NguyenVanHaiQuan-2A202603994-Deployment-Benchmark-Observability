# Deployment Information

## Public URL

https://production-ai-agent-kp4j.onrender.com

## Platform

Render (Blueprint `06-lab-complete/render.yaml`), region Oregon, gói free.

| Tài nguyên | Loại | Vai trò |
| --- | --- | --- |
| `production-ai-agent-kp4j` | Web service (Docker, `rootDir: 06-lab-complete`) | Agent FastAPI, health check `/health` |
| `agent-redis-kp4j` | Key Value (Redis) | State của rate limiter và cost guard |

Service build từ `06-lab-complete/Dockerfile` (multi-stage, `python:3.11-slim`, user non-root). Render cấp `PORT`, `CMD` của image đọc `${PORT}`.

## Test Commands

Đặt biến trước khi chạy (key lấy ở Render Dashboard → service → Environment, không ghi vào repo):

```bash
export BASE=https://production-ai-agent-kp4j.onrender.com
export AGENT_API_KEY=<key trên Render>
```

### Health / Readiness

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"   # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"    # 200 (Redis kết nối được)
curl -s "$BASE/health"
# {"status":"ok","version":"1.0.0","environment":"production","uptime_s":22.3}
```

### Authentication

```bash
# Không có key → 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -d '{"question":"Hello"}'

# Có key → 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" \
  -d '{"question":"Hello"}'
```

### Rate limit (10 request/phút → 429)

```bash
for i in $(seq 1 12); do
  curl -sS -o /dev/null -w '%{http_code} ' -X POST "$BASE/ask" \
    -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" \
    -d '{"user_id":"test","question":"test"}'
done; echo
```

## Kết quả tự kiểm (CP6)

| Lệnh | Mong đợi | Thực tế |
| --- | --- | --- |
| `GET /health` | 200 | 200 |
| `GET /ready` | 200 | 200 |
| `POST /ask` không key | 401 | 401 |
| `POST /ask` có key | 200 | _(điền sau khi chạy)_ |

`python check_production_ready.py --public-url https://production-ai-agent-kp4j.onrender.com` → `Public URL: GET /health → 200`.

## Environment Variables Set

Đặt trên Render qua Blueprint, không ghi giá trị secret trong code hay repo:

| Biến | Nguồn |
| --- | --- |
| `PORT` | Render tự cấp |
| `ENVIRONMENT` | `production` (render.yaml) |
| `AGENT_API_KEY` | `generateValue: true` — Render sinh ngẫu nhiên |
| `REDIS_URL` | `fromService` → connection string của `agent-redis-kp4j` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
