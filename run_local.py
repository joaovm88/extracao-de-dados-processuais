"""Roda o sistema SEM Docker (fila e busca em memória, tudo num só processo).

No PyCharm: botão direito neste arquivo > Run 'run_local'. Depois abra http://localhost:8000
Precisa de um arquivo .env na pasta (copie o .env.example) com a chave da IA."""
import os

from app.config import load_dotenv

if __name__ == "__main__":
    n = load_dotenv(".env")
    # modo sem Docker: sem Redis e sem Elasticsearch (valem os padrões "memory" a não ser que o .env diga outra coisa)
    os.environ.setdefault("QUEUE_BACKEND", "memory")
    os.environ.setdefault("SEARCH_BACKEND", "memory")
    print(f"Variáveis lidas do .env: {n}. Abra http://localhost:8000 (Ctrl+C para parar).")
    import uvicorn

    # 127.0.0.1: só este computador acessa (não expõe os processos na rede da instituição)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
