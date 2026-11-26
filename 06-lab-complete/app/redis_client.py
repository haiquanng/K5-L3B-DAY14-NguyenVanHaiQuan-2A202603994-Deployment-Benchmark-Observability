"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Client tạo "lazy": redis-py chỉ mở kết nối khi có lệnh đầu tiên,
    nên app vẫn khởi động được khi Redis chưa sẵn sàng.
  • Timeout ngắn để /ready trả 503 nhanh khi Redis chết, không treo request.
"""
import redis

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
