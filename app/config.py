import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        env = os.environ.get
        self.queue_backend = env("QUEUE_BACKEND", "memory")
        self.redis_url = env("REDIS_URL", "redis://localhost:6379/0")
        self.search_backend = env("SEARCH_BACKEND", "memory")
        self.es_url = env("ELASTICSEARCH_URL", "http://localhost:9200")
        self.es_index = env("ES_INDEX", "processos_chunks")
        # LLM: "auto" escolhe Gemini (Google AI Studio) se houver chave, senão Anthropic, senão nenhum
        self.llm_provider = env("LLM_PROVIDER", "auto").lower()
        self.gemini_api_key = env("GEMINI_API_KEY", "") or env("GOOGLE_API_KEY", "")
        self.gemini_model = env("GEMINI_MODEL", "gemini-3.1-pro-preview")
        self.anthropic_api_key = env("ANTHROPIC_API_KEY", "")
        self.llm_model = env("LLM_MODEL", "claude-sonnet-5-5")
        self.ocr_lang = env("OCR_LANG", "por")
        self.ocr_dpi = int(env("OCR_DPI", "200"))
        self.min_text_chars = int(env("MIN_TEXT_CHARS", "40"))
        self.data_dir = Path(env("DATA_DIR", "./data"))
        self.max_upload_mb = int(env("MAX_UPLOAD_MB", "500"))
        self.embedding_dims = 256

    @property
    def upload_dir(self) -> Path:
        d = self.data_dir / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / "metadata.sqlite3"
