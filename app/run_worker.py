"""Worker independente (QUEUE_BACKEND=redis): python -m app.run_worker"""
import logging
import threading

from .config import Settings
from .services import make_db, make_queue, make_store
from .worker import run_worker

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    s = Settings()
    run_worker(make_queue(s), s, make_db(s), make_store(s), threading.Event())
