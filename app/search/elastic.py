"""Elasticsearch: busca lexical (BM25, analisador português) + kNN vetorial em uma única consulta."""
from typing import Optional

from elasticsearch import Elasticsearch, helpers

from .base import Hit, embed


class ElasticStore:
    def __init__(self, url: str, index: str, dims: int = 256) -> None:
        self.es = Elasticsearch(url)
        self.index_name = index
        self.dims = dims
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

    def search(self, query: str, doc_id: Optional[str], k: int = 6) -> list[Hit]:
        flt = [{"term": {"doc_id": doc_id}}] if doc_id else []
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
