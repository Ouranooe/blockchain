"""迭代 8：运维可观测性与限流测试。"""

import os
import re

import pytest


def test_health_endpoint(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_liveness_endpoint(client):
    assert client.get("/health/live").json() == {"status": "alive"}


def test_readiness_probes_db(client):
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_metrics_endpoint_exposes_prometheus_format(client, make_user, login_token):
    # 先发几个请求让指标不为空
    make_user("m_user", password="p", role="patient")
    login_token("m_user", "p")
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    ct = resp.headers["content-type"]
    assert ct.startswith("text/plain")
    body = resp.text
    # 关键指标名出现
    assert "medshare_requests_total" in body
    assert "medshare_request_latency_seconds" in body
    # 至少一个方法为 GET
    assert re.search(r'medshare_requests_total\{method="GET"', body)


def test_metrics_counts_login_request(client, make_user, login_token):
    before = client.get("/metrics").text
    make_user("cntr", password="p", role="patient")
    client.post("/api/auth/login", json={"username": "cntr", "password": "p"})
    after = client.get("/metrics").text
    # 登录路径应出现在标签里
    assert '/api/auth/login' in after or "login" in after


class TestRateLimiter:
    def test_rate_limiter_disabled_in_tests(self, client, make_user):
        """默认测试环境限流关闭（ENVIRONMENT=test）→ 海量登录不被拦。"""
        make_user("rate_user", password="p", role="patient")
        # 100 次应全部 2xx（超过生产配置的 10/min）
        success = 0
        for _ in range(30):
            r = client.post(
                "/api/auth/login", json={"username": "rate_user", "password": "p"}
            )
            if r.status_code == 200:
                success += 1
        assert success == 30


def test_rate_limiter_blocks_when_enabled(monkeypatch, client_with_limiter, make_user):
    """开启限流后，超过阈值返回 429。"""
    make_user("rate_user_2", password="p", role="patient")
    statuses = []
    for _ in range(15):
        r = client_with_limiter.post(
            "/api/auth/login",
            json={"username": "rate_user_2", "password": "p"},
        )
        statuses.append(r.status_code)
    assert 429 in statuses, f"没有 429：{statuses}"
