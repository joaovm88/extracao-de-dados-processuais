"""Provedores de LLM intercambiáveis: Google Gemini (AI Studio) e Anthropic.

Contrato: `extract(system, user, schema)` devolve um dict que obedece ao JSON Schema;
`generate(system, user)` devolve texto livre (chat RAG)."""
import json
import math
import re
import time
from typing import Optional, Protocol

from .config import Settings


class LLMProvider(Protocol):
    name: str

    def extract(self, system: str, user: str, schema: dict) -> dict: ...
    def generate(self, system: str, user: str, max_tokens: int = 1000) -> str: ...


def _retry_delay(e: Exception) -> Optional[float]:
    """Limite POR MINUTO (429 com 'retry in 32s'): vale esperar e repetir. Cota diária ou sem prazo: não."""
    txt = str(e)
    if "429" not in txt and "RESOURCE_EXHAUSTED" not in txt:
        return None
    m = re.search(r"retry in ([\d.]+)\s*s", txt, re.IGNORECASE) or re.search(r"retryDelay['\"]?:\s*['\"]?([\d.]+)s", txt)
    if not m:
        return None
    delay = math.ceil(float(m.group(1))) + 1
    return delay if delay <= 75 else None


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, client=None) -> None:
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self.client, self.model = client, model

    def _call(self, **kw):
        """generate_content com até 2 novas tentativas quando o limite por minuto estoura."""
        for attempt in range(3):
            try:
                return self.client.models.generate_content(**kw)
            except Exception as e:  # noqa: BLE001
                delay = _retry_delay(e)
                if delay is None or attempt == 2:
                    raise
                time.sleep(delay)

    def _config(self, system: str, **kw):
        from google.genai import types

        return types.GenerateContentConfig(system_instruction=system, temperature=0.1, **kw)

    def extract(self, system: str, user: str, schema: dict) -> dict:
        try:
            resp = self._call(
                model=self.model, contents=user,
                config=self._config(system, response_mime_type="application/json", response_json_schema=schema,
                                    max_output_tokens=16000))
        except Exception as e:  # noqa: BLE001  esquema recusado pela API: cai para JSON livre + validação local
            if "schema" not in str(e).lower() and "INVALID_ARGUMENT" not in str(e):
                raise
            hint = "\n\nResponda SOMENTE com um objeto JSON com estas propriedades: " + json.dumps(schema["properties"], ensure_ascii=False)
            resp = self._call(
                model=self.model, contents=user,
                config=self._config(system + hint, response_mime_type="application/json", max_output_tokens=16000))
        return _loads(resp.text)

    def generate(self, system: str, user: str, max_tokens: int = 1000) -> str:
        resp = self._call(
            model=self.model, contents=user, config=self._config(system, max_output_tokens=max_tokens))
        return resp.text or ""


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, client=None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
        self.client, self.model = client, model

    def extract(self, system: str, user: str, schema: dict) -> dict:
        msg = self.client.messages.create(
            model=self.model, max_tokens=8000, system=system,
            tools=[{"name": "registrar_extracao", "description": "Registra os dados extraídos do processo.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": "registrar_extracao"},
            messages=[{"role": "user", "content": user}])
        for b in msg.content:
            if getattr(b, "type", "") == "tool_use":
                return dict(b.input)
        raise RuntimeError("O modelo não devolveu a extração estruturada.")

    def generate(self, system: str, user: str, max_tokens: int = 1000) -> str:
        msg = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                          messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _loads(text: Optional[str]) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(t)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Resposta do modelo não é JSON válido: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError("Resposta do modelo não é um objeto JSON.")
    return data


def get_llm(s: Settings) -> Optional[LLMProvider]:
    p = s.llm_provider
    if p == "gemini" or (p == "auto" and s.gemini_api_key):
        return GeminiProvider(s.gemini_api_key, s.gemini_model) if s.gemini_api_key else None
    if p == "anthropic" or (p == "auto" and s.anthropic_api_key):
        return AnthropicProvider(s.anthropic_api_key, s.llm_model) if s.anthropic_api_key else None
    return None
