"""Acesso por senha para quando o sistema fica na internet (deploy de teste).

Ative definindo APP_USERS="nome:senha,outro:senha2" (ou só APP_PASSWORD="senha", usuário "analista").
Sem nenhuma das duas, o sistema funciona como antes (uso local, sem login).
A sessão é um cookie assinado (HMAC) que vale APP_SESSION_HOURS horas (padrão 12)."""
import hashlib
import hmac
import os
import time
from typing import Optional

COOKIE = "sessao"
LIVRES = ("/api/health", "/api/login", "/api/logout", "/login.html", "/app.css", "/favicon.ico")


def usuarios() -> dict[str, str]:
    bruto = os.environ.get("APP_USERS", "").strip()
    out = {}
    for par in bruto.split(","):
        if ":" in par:
            u, p = par.split(":", 1)
            if u.strip() and p:
                out[u.strip()] = p
    if not out and os.environ.get("APP_PASSWORD"):
        out["analista"] = os.environ["APP_PASSWORD"]
    return out


def ativo() -> bool:
    return bool(usuarios())


def _segredo() -> bytes:
    base = os.environ.get("APP_SECRET") or "|".join(f"{u}:{p}" for u, p in sorted(usuarios().items()))
    return hashlib.sha256(("sessao-v1|" + base).encode()).digest()  # trocar a senha derruba as sessões antigas


def horas() -> float:
    return float(os.environ.get("APP_SESSION_HOURS", "12"))


def emitir(usuario: str) -> str:
    exp = int(time.time() + horas() * 3600)
    msg = f"{usuario}|{exp}"
    return msg + "|" + hmac.new(_segredo(), msg.encode(), hashlib.sha256).hexdigest()


def validar(token: Optional[str]) -> Optional[str]:
    try:
        usuario, exp, sig = (token or "").rsplit("|", 2)
    except ValueError:
        return None
    esperado = hmac.new(_segredo(), f"{usuario}|{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, esperado) or int(exp) < time.time() or usuario not in usuarios():
        return None
    return usuario


def confere(usuario: str, senha: str) -> bool:
    certa = usuarios().get(usuario or "")
    return certa is not None and hmac.compare_digest(certa.encode(), (senha or "").encode())
