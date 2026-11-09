"""Provedores de LLM intercambiáveis: Google Gemini (AI Studio) e Anthropic.

Contrato: `extract(system, user, schema)` devolve um dict que obedece ao JSON Schema;
`generate(system, user)` devolve texto livre (chat RAG)."""
import json
import math
import re
import threading
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


TRANSIENT_BACKOFF = (10, 25, 50)


def _is_transient(e: Exception) -> bool:
    """Sobrecarga/indisponibilidade momentânea do serviço (HTTP 503/502/504)."""
    txt = str(e).lower()
    return any(k in txt for k in ("503", "502", "504", "unavailable", "high demand", "overloaded",
                                  "timed out", "timeout", "deadline", "disconnected", "connection reset"))


def _resumo_erro(e: Exception) -> str:
    t = str(e).lower()
    for k, v in (("503", "503"), ("429", "429"), ("timeout", "timeout"), ("timed out", "timeout"), ("deadline", "timeout"), ("504", "504"), ("502", "502")):
        if k in t:
            return v
    return "erro"


class _UsageMixin:
    """Tokens da última chamada bem-sucedida, por thread (a API e os workers compartilham o provedor)."""
    _tls = None

    def _set_usage(self, entrada, saida, raciocinio=None) -> None:
        if self._tls is None:
            self._tls = threading.local()
        self._tls.u = {"entrada": entrada, "saida": saida, "raciocinio": raciocinio}

    @property
    def last_usage(self) -> Optional[dict]:
        return getattr(self._tls, "u", None) if self._tls is not None else None

    def _set_call(self, tentativas: int, espera_s: float, modelo: str, tempos: Optional[list] = None) -> None:
        if self._tls is None:
            self._tls = threading.local()
        self._tls.c = {"tentativas": tentativas, "espera_s": round(espera_s, 1), "modelo": modelo, "tempos": tempos or []}

    @property
    def last_call(self) -> Optional[dict]:
        """Da última chamada bem-sucedida: quantas tentativas foram feitas, quanto tempo se esperou entre elas e o modelo que respondeu."""
        return getattr(self._tls, "c", None) if self._tls is not None else None


class GeminiProvider(_UsageMixin):
    name = "gemini"

    def __init__(self, api_key: str, model: str, client=None, fallback_model: str = "", thinking: str = "",
                 timeout_s: float = 0, thinking_fallback: str = "") -> None:
        if client is None:
            from google import genai
            from google.genai import types

            # timeout por tentativa: uma chamada travada não pode ocupar minutos sem que a nova tentativa comece
            opts = types.HttpOptions(timeout=int(timeout_s * 1000)) if timeout_s else None
            client = genai.Client(api_key=api_key, http_options=opts)
        self.client, self.model, self.fallback_model = client, model, fallback_model
        self.thinking = (thinking or "").strip().lower()
        self.thinking_fallback = (thinking_fallback or "").strip().lower()
        self._nivel: dict[str, str] = {}  # nível efetivo por modelo (rebaixado se o modelo recusar o pedido)

    def _nivel_de(self, model: str) -> str:
        if model in self._nivel:
            return self._nivel[model]
        return self.thinking_fallback if (self.fallback_model and model == self.fallback_model and self.thinking_fallback) else self.thinking

    def _thinking_cfg(self, model: str):
        """Esforço de raciocínio do modelo ('minimal'/'low'/'medium'/'high'; vazio = padrão do modelo). Menos raciocínio = resposta mais rápida.
        O modelo reserva pode ter o seu próprio nível (GEMINI_THINKING_FALLBACK): nem todo modelo aceita 'minimal'."""
        nivel = self._nivel_de(model)
        if nivel not in ("minimal", "low", "medium", "high"):
            return None
        from google.genai import types

        m = model.lower()
        if "gemini-3" in m:
            return types.ThinkingConfig(thinking_level=nivel)
        if "2.5" in m:
            return types.ThinkingConfig(thinking_budget={"minimal": 512, "low": 1024, "medium": 4096, "high": -1}[nivel])
        return None

    def _call(self, patient: bool = True, **kw):
        """generate_content com novas tentativas. Extração (patient): até 4 tentativas, esperando 10/25/50 s em sobrecarga 503
        e o prazo do Google em limite por minuto. Chat (impaciente): 2 tentativas com 5 s, para o analista não ficar esperando.
        Esgotadas, tenta o modelo reserva, se houver. Erros que esperar não resolve sobem na hora."""
        models = [kw["model"]]
        if self.fallback_model and self.fallback_model != kw["model"]:
            models.append(self.fallback_model)
        tentativas, backoff = (4, TRANSIENT_BACKOFF) if patient else (2, (5,))
        last: Optional[Exception] = None
        feitas, esperou, tempos = 0, 0.0, []
        base_cfg = kw.get("config")
        for model in models:
            kw["model"] = model
            if base_cfg is not None:
                upd = {"thinking_config": self._thinking_cfg(model)}
                if "gemini-3" in model.lower():
                    upd["temperature"] = None  # família 3: amostragem fora do pedido (valores não padrão podem dar 400); vale o padrão do modelo
                kw["config"] = base_cfg.model_copy(update=upd)
            for attempt in range(tentativas):
                t0 = time.perf_counter()
                try:
                    feitas += 1
                    resp = self.client.models.generate_content(**kw)
                    tempos.append({"s": round(time.perf_counter() - t0, 1), "r": "ok"})
                    self._set_call(feitas, esperou, model, tempos)
                    return resp
                except Exception as e:  # noqa: BLE001
                    last = e
                    tempos.append({"s": round(time.perf_counter() - t0, 1), "r": _resumo_erro(e)})
                    if "thinking" in str(e).lower() and "INVALID_ARGUMENT" in str(e) and kw.get("config") is not None \
                            and getattr(kw["config"], "thinking_config", None) is not None:
                        atual = self._nivel_de(model)
                        self._nivel[model] = "low" if atual == "minimal" else ""  # 'minimal' recusado: tenta 'low'; senão, sem controle
                        kw["config"] = kw["config"].model_copy(update={"thinking_config": self._thinking_cfg(model)})
                        continue
                    delay = _retry_delay(e)
                    transient = _is_transient(e)
                    if delay is None and not transient:
                        raise  # erro que esperar não resolve (chave, modelo, cota diária, pedido inválido)
                    if not patient and delay is not None and delay > 20:
                        raise  # no chat não vale esperar tanto: o analista tenta de novo
                    if attempt == tentativas - 1:
                        break
                    espera = delay if delay is not None else backoff[attempt]
                    esperou += espera
                    time.sleep(espera)
        assert last is not None
        raise last

    def _note_usage(self, resp) -> None:
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            self._set_usage(getattr(um, "prompt_token_count", None), getattr(um, "candidates_token_count", None),
                            getattr(um, "thoughts_token_count", None))

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
        self._note_usage(resp)
        return _loads(resp.text)

    def generate(self, system: str, user: str, max_tokens: int = 1000) -> str:
        resp = self._call(
            patient=False, model=self.model, contents=user, config=self._config(system, max_output_tokens=max_tokens))
        self._note_usage(resp)
        return resp.text or ""


class AnthropicProvider(_UsageMixin):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, client=None, effort: str = "") -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
        self.client, self.model = client, model
        self.effort = (effort or "").strip().lower()

    def _effort_kw(self) -> dict:
        """Esforço de raciocínio (low|medium|high|xhigh|max) só nos modelos da família 5.x, que o aceitam."""
        if self.effort in ("low", "medium", "high", "xhigh", "max") and "-5" in self.model:
            return {"output_config": {"effort": self.effort}}
        return {}

    def extract(self, system: str, user: str, schema: dict) -> dict:
        # tool_choice 'auto' + instrução: os modelos 5.x recusam (400) a escolha forçada de ferramenta
        msg = self.client.messages.create(
            model=self.model, max_tokens=8000, system=system + "\n\nPara responder, chame SEMPRE a ferramenta registrar_extracao.",
            tools=[{"name": "registrar_extracao", "description": "Registra os dados extraídos do processo.", "input_schema": schema}],
            tool_choice={"type": "auto"}, messages=[{"role": "user", "content": user}], **self._effort_kw())
        u = getattr(msg, "usage", None)
        if u is not None:
            self._set_usage(getattr(u, "input_tokens", None), getattr(u, "output_tokens", None))
        for b in msg.content:
            if getattr(b, "type", "") == "tool_use":
                return dict(b.input)
        raise RuntimeError("O modelo não devolveu a extração estruturada.")

    def generate(self, system: str, user: str, max_tokens: int = 1000) -> str:
        msg = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                          messages=[{"role": "user", "content": user}])
        u = getattr(msg, "usage", None)
        if u is not None:
            self._set_usage(getattr(u, "input_tokens", None), getattr(u, "output_tokens", None))
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
        return GeminiProvider(s.gemini_api_key, s.gemini_model, fallback_model=s.gemini_fallback_model,
                                              thinking=s.gemini_thinking, timeout_s=s.gemini_timeout_s,
                                              thinking_fallback=s.gemini_thinking_fallback) if s.gemini_api_key else None
    if p == "anthropic" or (p == "auto" and s.anthropic_api_key):
        return AnthropicProvider(s.anthropic_api_key, s.llm_model, effort=s.llm_effort) if s.anthropic_api_key else None
    return None
