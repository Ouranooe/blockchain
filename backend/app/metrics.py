"""迭代 8：Prometheus 指标与请求埋点中间件。

暴露的指标（路径前缀 medshare_）：
  - requests_total{method, path, status_code}  计数
  - request_latency_seconds{method, path}      直方图
  - chain_calls_total{operation}               链码调用累计
  - chain_call_errors_total{operation}         链码失败累计
  - ws_connections                             活跃 WebSocket 数
  - audit_events_total                         已落库审计数（来自 bus.stats）
"""

from __future__ import annotations

import time
from typing import Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

registry = CollectorRegistry()

REQUEST_COUNTER = Counter(
    "medshare_requests_total",
    "HTTP 请求总数",
    ["method", "path", "status"],
    registry=registry,
)

REQUEST_LATENCY = Histogram(
    "medshare_request_latency_seconds",
    "HTTP 请求延迟（秒）",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=registry,
)

CHAIN_CALLS = Counter(
    "medshare_chain_calls_total",
    "链码调用累计次数",
    ["operation", "outcome"],
    registry=registry,
)

WS_CONNECTIONS = Gauge(
    "medshare_ws_connections",
    "当前活跃 WebSocket 连接数",
    registry=registry,
)

AUDIT_EVENTS_EMITTED = Gauge(
    "medshare_audit_events_emitted",
    "事件总线累计 emit 条数（自启动）",
    registry=registry,
)


def _normalize_path(request: Request) -> str:
    """把实际路径归一化为路由模板（避免 label 爆炸）。"""
    route = request.scope.get("route")
    if route and hasattr(route, "path"):
        return route.path
    # fallback：截断纯数字段
    path = request.url.path
    parts = []
    for seg in path.split("/"):
        if seg.isdigit():
            parts.append(":id")
        else:
            parts.append(seg)
    return "/".join(parts)


async def metrics_middleware(request: Request, call_next: Callable):
    start = time.perf_counter()
    response: Response
    try:
        response = await call_next(request)
        status = str(response.status_code)
    except Exception:
        status = "500"
        raise
    finally:
        elapsed = time.perf_counter() - start
        path = _normalize_path(request)
        REQUEST_COUNTER.labels(request.method, path, status).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(elapsed)
    return response


def install_metrics(app: FastAPI) -> None:
    from fastapi.responses import Response as FastAPIResponse

    app.middleware("http")(metrics_middleware)

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint():
        # 把事件总线的 stats 同步到 Gauge
        try:
            from .events import bus

            AUDIT_EVENTS_EMITTED.set(bus.stats.get("emitted", 0))
        except Exception:
            pass
        return FastAPIResponse(
            content=generate_latest(registry),
            media_type=CONTENT_TYPE_LATEST,
        )
