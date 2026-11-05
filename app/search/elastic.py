"""Elasticsearch: busca lexical (BM25, analisador português) + kNN vetorial em uma única consulta."""
import logging
import time
from typing import Optional

from elasticsearch import Elasticsearch, helpers

from .base import Hit, embed


log = logging.getLogger("elastic")


class ElasticStore:
    def __init__(self, url: str, index: str, dims: int = 256, wait_seconds: int = 180) -> None:
        self.es = Elasticsearch(url)
        self.index_name = index
        self.dims = dims
        self._wait_ready(url, wait_seconds)
        if not self.es.indices.exists(index=index):
            self.es.indices.create(
                index=index,
                mappings={
                    "properties": {
                        "doc_id": {"type": "keyword"},
                        "page": {"type": "integer"},
                        "text": {"type": "text", "analyzer": "portuguese"},
                        "embedding": {"type": "dense_vector", "dims": dims, "index": True, "similarity": "cosine"},
                    }
                },
            )

    def _wait_ready(self, url: str, wait_seconds: int) -> None:
        """O Elasticsearch leva de 30 a 60 s para subir: espera em vez de derrubar a aplicação."""
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                self.es.cluster.health(wait_for_status="yellow", timeout="5s")
                return
            except Exception as e:  # noqa: BLE001
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Elasticsearch indisponível em {url} após {wait_seconds}s: {e}") from e
                log.warning("Aguardando o Elasticsearch em %s…", url)
                time.sleep(3)

    def index(self, doc_id: str, chunks: list) -> None:
        actions = (
            {
                "_index": self.index_name,
                "_source": {"doc_id": doc_id, "page": c.page, "text": c.text, "embedding": embed(c.text, self.dims)},
            }
            for c in chunks
        )
        helpers.bulk(self.es, actions, refresh="wait_for")

    def delete(self, doc_id: str) -> None:
        self.es.delete_by_query(index=self.index_name, query={"term": {"doc_id": doc_id}}, refresh=True)

    def search(self, query: str, doc_ids: Optional[list[str]], k: int = 6) -> list[Hit]:
        flt = [{"terms": {"doc_id": doc_ids}}] if doc_ids else []
        resp = self.es.search(
            index=self.index_name,
            size=k,
            query={"bool": {"must": [{"match": {"text": query}}], "filter": flt}},
            knn={"field": "embedding", "query_vector": embed(query, self.dims), "k": k,
                 "num_candidates": max(50, k * 10), "filter": flt},
        )
        return [
            Hit(h["_source"]["doc_id"], h["_source"]["page"], h["_source"]["text"], h["_score"])
            for h in resp["hits"]["hits"]
        ]
