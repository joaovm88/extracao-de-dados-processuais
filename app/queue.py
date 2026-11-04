"""Fila assíncrona: Redis (produção) ou thread em memória (dev/testes)."""
import queue as _q
import threading
from typing import Callable


class MemoryQueue:
    def __init__(self) -> None:
        self._q: "_q.Queue[str]" = _q.Queue()

    def enqueue(self, doc_id: str) -> None:
        self._q.put(doc_id)

    def consume(self, handler: Callable[[str], None], stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                doc_id = self._q.get(timeout=0.5)
            except _q.Empty:
                continue
            try:
                handler(doc_id)
            finally:
                self._q.task_done()

    def join(self) -> None:
        self._q.join()


class RedisQueue:
    KEY = "processos:ingest"

    def __init__(self, url: str) -> None:
        import redis

        self.r = redis.Redis.from_url(url, decode_responses=True)

    def enqueue(self, doc_id: str) -> None:
        self.r.lpush(self.KEY, doc_id)

    def consume(self, handler: Callable[[str], None], stop: threading.Event) -> None:
        while not stop.is_set():
            item = self.r.brpop(self.KEY, timeout=2)
            if item:
                handler(item[1])
