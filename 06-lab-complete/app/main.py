"""
Production AI Agent — Part 6 Final Project.

Endpoints:
    GET  /health  liveness  — luôn 200 khi process còn sống
    GET  /ready   readiness — 200 khi Redis kết nối được và chưa shutdown, ngược lại 503
    POST /ask     X-API-Key → rate limit → budget → LLM → ghi cost
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask

VERSION = "1.0.0"


# ── Structured logging: mỗi dòng log là 1 JSON object ──
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False
    return log


logger = _setup_logging()


def log(level: int, event: str, **fields) -> None:
    logger.log(level, event, extra={"extra_fields": fields})


# ── Lifecycle / graceful shutdown ──
# uvicorn tự bắt SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong,
# rồi chạy phần sau `yield` của lifespan. Ta không ghi đè signal handler của uvicorn.
STATE = {"started_at": time.time(), "shutting_down": False}


@asynccontextmanager
async def lifespan(_: FastAPI):
    log(logging.INFO, "startup", environment=settings.environment, version=VERSION,
        redis_ok=redis_healthy())
    yield
    STATE["shutting_down"] = True
    log(logging.INFO, "shutdown", msg="draining finished, closing Redis connection")
    try:
        r.close()
    except Exception:  # noqa: BLE001 — không để lỗi dọn dẹp làm exit code khác 0
        pass


app = FastAPI(title="Production AI Agent", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log(logging.ERROR, "unhandled_error", request_id=request_id, path=request.url.path)
        response = JSONResponse(status_code=500, content={"error": "Internal server error",
                                                          "request_id": request_id})
    response.headers["X-Request-ID"] = request_id
    log(logging.INFO, "request", request_id=request_id, method=request.method,
        path=request.url.path, status=response.status_code,
        ms=round((time.perf_counter() - start) * 1000, 1))
    return response


# ── Error handling: mã lỗi rõ ràng, không lộ stack trace ──
@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail},
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": "Invalid request body", "details": errors})


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    log(logging.ERROR, "unhandled_error", exc_type=type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ── Routes ──
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION, "environment": settings.environment,
            "uptime_s": round(time.time() - STATE["started_at"], 1)}


@app.get("/ready")
def ready():
    redis_ok = redis_healthy()
    if STATE["shutting_down"] or not redis_ok:
        return JSONResponse(status_code=503, content={
            "status": "not_ready", "redis": "ok" if redis_ok else "unreachable",
            "shutting_down": STATE["shutting_down"]})
    return {"status": "ready", "redis": "ok"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    question = body.question.strip()
    if not question:
        return JSONResponse(status_code=422, content={"error": "question must not be blank"})

    check_rate_limit(api_key)
    check_budget()

    start = time.perf_counter()
    answer = llm_ask(question)
    input_tokens, output_tokens = estimate_tokens(question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log(logging.INFO, "llm_call", llm_ms=round((time.perf_counter() - start) * 1000, 1),
        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=round(cost, 8))

    return {"question": question, "answer": answer,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                      "cost_usd": round(cost, 8)}}
