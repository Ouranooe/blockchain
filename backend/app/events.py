"""迭代 6：后端事件总线与 WebSocket 通知中心。

工作流：
  1. 业务流成功上链后 → 调用 emit_event(...) 投递一条事件
  2. 同步路径：立即写 AuditEventRow（批量写队列，≤1s 或 ≥100 条 flush）
  3. 异步路径：推给所有"相关用户"的已连接 WebSocket（每个用户多连接共享）

在真实 Fabric 部署下，gateway 侧 contract.addContractListener() 捕获链码事件后，
应调用后端的 POST /internal/events 回传给 emit_event —— 这样链码事件才是第一事实源。
本迭代为简化采用"应用层 publish"实现，语义等价。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import AuditEventRow

logger = logging.getLogger(__name__)


@dataclass
class AuditEvent:
    event_type: str
    actor_id: Optional[int] = None
    actor_role: Optional[str] = None
    subject_user_id: Optional[int] = None  # 通知目标（主要接收人）
    extra_subject_ids: List[int] = field(default_factory=list)  # 其他需要接收的用户
    record_id: Optional[int] = None
    request_id: Optional[int] = None
    tx_id: Optional[str] = None
    message: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def serialize_for_ws(self) -> dict:
        return {
            "event_type": self.event_type,
            "record_id": self.record_id,
            "request_id": self.request_id,
            "tx_id": self.tx_id,
            "message": self.message,
            "payload": self.payload,
            "timestamp_ms": self.timestamp_ms,
        }


class EventBus:
    """进程内 asyncio broadcaster + 审计批量写队列。"""

    BATCH_MAX = 100
    BATCH_FLUSH_SECONDS = 1.0

    def __init__(self) -> None:
        self._ws_subscribers: Dict[int, Set[asyncio.Queue]] = {}
        self._admin_subscribers: Set[asyncio.Queue] = set()
        self._lock: Optional[asyncio.Lock] = None
        self._audit_queue: "asyncio.Queue[AuditEvent]" = None  # type: ignore
        self._flusher_task: Optional[asyncio.Task] = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        # 统计：供测试断言
        self.stats = {"emitted": 0, "broadcast": 0, "persisted": 0}
        # 可注入替身持久化（测试友好）
        self._persist: Callable[[List[AuditEvent]], None] = self._default_persist

    async def start(self) -> None:
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = asyncio.get_event_loop()
        if self._audit_queue is None:
            self._audit_queue = asyncio.Queue()
        if self._lock is None:
            self._lock = asyncio.Lock()
        if self._flusher_task is None or self._flusher_task.done():
            self._flusher_task = asyncio.create_task(self._flusher_loop())

    async def stop(self) -> None:
        if self._flusher_task and not self._flusher_task.done():
            self._flusher_task.cancel()
            try:
                await self._flusher_task
            except asyncio.CancelledError:
                pass

    # -------- 订阅 / 取消订阅 --------

    async def subscribe(self, user_id: int, is_admin: bool) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._ws_subscribers.setdefault(user_id, set()).add(q)
            if is_admin:
                self._admin_subscribers.add(q)
        return q

    async def unsubscribe(self, user_id: int, q: asyncio.Queue) -> None:
        async with self._lock:
            if user_id in self._ws_subscribers:
                self._ws_subscribers[user_id].discard(q)
                if not self._ws_subscribers[user_id]:
                    del self._ws_subscribers[user_id]
            self._admin_subscribers.discard(q)

    # -------- 发布 --------

    async def _broadcast(self, event: AuditEvent) -> None:
        targets: Set[int] = set()
        if event.subject_user_id is not None:
            targets.add(event.subject_user_id)
        for uid in event.extra_subject_ids:
            targets.add(uid)

        payload = event.serialize_for_ws()
        async with self._lock:
            queues_to_push: List[asyncio.Queue] = []
            for uid in targets:
                for q in list(self._ws_subscribers.get(uid, ())):
                    queues_to_push.append(q)
            for q in list(self._admin_subscribers):
                if q not in queues_to_push:
                    queues_to_push.append(q)

        for q in queues_to_push:
            try:
                q.put_nowait(payload)
                self.stats["broadcast"] += 1
            except asyncio.QueueFull:
                logger.warning("事件队列已满，丢弃一条（订阅者可能阻塞）")

    async def emit(self, event: AuditEvent) -> None:
        """异步入口：广播 + 排队落库（由 flusher 批量写）。"""
        self.stats["emitted"] += 1
        await self._broadcast(event)
        await self._audit_queue.put(event)

    def emit_sync(self, event: AuditEvent) -> None:
        """业务同步路径的入口：同步落库 + 尝试异步广播（用 start() 时捕获的主 loop）。"""
        self.stats["emitted"] += 1
        # 1) 同步持久化（让查询接口立即能读到）
        try:
            self._persist([event])
            self.stats["persisted"] += 1
        except Exception:
            logger.exception("sync persist failed")
        # 2) 若主 loop 可用，安排一次异步广播
        loop = self._main_loop
        if loop is not None and not loop.is_closed():
            try:
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(self._broadcast(event), loop)
                else:
                    # 当前线程没在事件循环中；退化为同步广播（给有订阅者的队列 put_nowait）
                    targets: Set[int] = set()
                    if event.subject_user_id is not None:
                        targets.add(event.subject_user_id)
                    for uid in event.extra_subject_ids:
                        targets.add(uid)
                    payload = event.serialize_for_ws()
                    queues_to_push: List[asyncio.Queue] = []
                    for uid in targets:
                        for q in list(self._ws_subscribers.get(uid, ())):
                            queues_to_push.append(q)
                    for q in list(self._admin_subscribers):
                        if q not in queues_to_push:
                            queues_to_push.append(q)
                    for q in queues_to_push:
                        try:
                            q.put_nowait(payload)
                            self.stats["broadcast"] += 1
                        except asyncio.QueueFull:
                            pass
            except Exception:
                logger.exception("schedule broadcast failed")

    # -------- 批量写库 --------

    async def _flusher_loop(self) -> None:
        pending: List[AuditEvent] = []
        last_flush = time.monotonic()
        try:
            while True:
                timeout = max(0.0, self.BATCH_FLUSH_SECONDS - (time.monotonic() - last_flush))
                try:
                    ev = await asyncio.wait_for(self._audit_queue.get(), timeout=timeout or 0.01)
                    pending.append(ev)
                except asyncio.TimeoutError:
                    pass

                should_flush = len(pending) >= self.BATCH_MAX or (
                    pending and (time.monotonic() - last_flush) >= self.BATCH_FLUSH_SECONDS
                )
                if should_flush:
                    to_flush = pending
                    pending = []
                    last_flush = time.monotonic()
                    try:
                        self._persist(to_flush)
                        self.stats["persisted"] += len(to_flush)
                    except Exception as exc:
                        logger.exception("批量落库失败: %s", exc)
        except asyncio.CancelledError:
            if pending:
                try:
                    self._persist(pending)
                    self.stats["persisted"] += len(pending)
                except Exception as exc:
                    logger.exception("停机 flush 失败: %s", exc)
            raise

    # -------- 可注入持久化（测试支持） --------

    def set_persister(self, fn: Callable[[List[AuditEvent]], None]) -> None:
        self._persist = fn

    def _default_persist(self, events: List[AuditEvent]) -> None:
        if not events:
            return
        session: Session = SessionLocal()
        try:
            rows = [
                AuditEventRow(
                    event_type=e.event_type,
                    actor_id=e.actor_id,
                    actor_role=e.actor_role,
                    subject_user_id=e.subject_user_id,
                    record_id=e.record_id,
                    request_id=e.request_id,
                    tx_id=e.tx_id,
                    message=e.message,
                    payload_json=json.dumps(e.payload, ensure_ascii=False),
                )
                for e in events
            ]
            session.bulk_save_objects(rows)
            session.commit()
        finally:
            session.close()


bus = EventBus()
