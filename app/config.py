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
        # Modelo reserva usado só quando o principal fica sobrecarregado (503) após as novas tentativas. Opcional.
        self.gemini_fallback_model = env("GEMINI_FALLBACK_MODEL", "")
        # Esforço de raciocínio do Gemini: minimal | low | medium | high | (vazio = padrão do modelo). "low" acelera bastante a extração.
        self.gemini_thinking = env("GEMINI_THINKING", "low")
        # Nível do modelo reserva (alguns modelos não aceitam "minimal"). Vazio = usa o mesmo de GEMINI_THINKING.
        self.gemini_thinking_fallback = env("GEMINI_THINKING_FALLBACK", "")
        # Tempo máximo de cada tentativa (s). Passou disso, tenta de novo em vez de ficar esperando uma chamada travada.
        self.gemini_timeout_s = float(env("GEMINI_TIMEOUT_S", "240"))
        self.anthropic_api_key = env("ANTHROPIC_API_KEY", "")
        self.llm_model = env("LLM_MODEL", "claude-sonnet-5-5")
        # Esforço de raciocínio do Claude (modelos 5.x): low | medium | high | xhigh | max. Menos = mais rápido e barato.
        self.llm_effort = env("LLM_EFFORT", "low")
        # Texto máximo enviado ao LLM por processo (≈ 4 caracteres por token). Reduza no nível gratuito do Gemini.
        self.context_budget_chars = int(env("CONTEXT_BUDGET_CHARS", "400000"))
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
