"""Composição dos componentes conforme a configuração."""
from .config import Settings
from .db import MetadataDB
from .queue import MemoryQueue, RedisQueue
from .search.memory import MemoryStore


def make_store(s: Settings):
    if s.search_backend == "elasticsearch":
        from .search.elastic import ElasticStore

        return ElasticStore(s.es_url, s.es_index, s.embedding_dims)
    return MemoryStore(s.embedding_dims)


def make_queue(s: Settings):
    return RedisQueue(s.redis_url) if s.queue_backend == "redis" else MemoryQueue()


def make_db(s: Settings) -> MetadataDB:
    return MetadataDB(s.db_path)
