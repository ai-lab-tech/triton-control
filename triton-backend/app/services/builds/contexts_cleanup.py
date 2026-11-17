"""Bounded retention sweep. Schedule the CLI in the installation, e.g. hourly."""

import logging
import time
from threading import Event, Thread
from typing import Callable

from sqlmodel import Session

from app.repositories import build_contexts as repo
from app.repositories import builds, operations
from app.services.builds.contexts_storage import storage


def cleanup(sessions: Callable[[], Session], limit: int = 100) -> dict[str, int]:
    removed = failed = 0
    with sessions() as session:
        ids = repo.due(session, operations.now(session), limit)
    for context_id in ids:
        try:
            with sessions() as session:
                row = repo.get(session, context_id, lock=True)
                stamp = operations.now(session)
                if row.expired or row.expires_at > stamp or (row.upload_until and row.upload_until > stamp):
                    continue
                if builds.holds_context(session, context_id):
                    continue
                started = time.monotonic()

                def checkpoint() -> None:
                    if time.monotonic() - started > 60:
                        raise TimeoutError("Cleanup deadline exceeded")

                with storage(session, row.storage_binding) as store:
                    store.delete_prefix(row.storage_binding["prefix"] + row.id + "/", checkpoint)
                row.expired, row.upload_token = True, None
                session.add(row)
                session.commit()
                removed += 1
        except Exception:
            # No provider text/credentials in console or database. Leave the row
            # eligible for a later sweep after partial deletion or storage outages.
            failed += 1
    return {"removed": removed, "failed": failed}


class RetentionWorker:
    """Small bounded sweeps; PostgreSQL row locks serialize multiple replicas."""

    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions
        self.stopped = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        if isinstance(self.thread, Thread) and self.thread.is_alive():
            return
        self.stopped.clear()
        self.thread = Thread(target=self.loop, daemon=True, name="build-context-retention")
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        if isinstance(self.thread, Thread):
            self.thread.join(timeout=5)

    def loop(self) -> None:
        while not self.stopped.is_set():
            try:
                result = cleanup(self.sessions, limit=10)
                with self.sessions() as session:
                    builds.expire_logs(session, operations.now(session))
                if result["failed"]:
                    logging.getLogger(__name__).warning("Some build context cleanup attempts will be retried")
            except Exception:
                logging.getLogger(__name__).warning("Build context retention sweep will retry")
            self.stopped.wait(300)
