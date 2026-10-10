"""Reconcile legacy MLflow installations without delaying backend startup."""

import asyncio
import logging

from app.db.database import session_factory
from app.repositories import mlflow
from app.services.mlflow import installer, kubernetes

logger = logging.getLogger(__name__)
_task: asyncio.Task[None] | None = None


def reconcile() -> None:
    with session_factory() as session:
        entity = mlflow.get(session)
        if entity is None or entity.status == "deleting":
            return
        namespace, deployment_name = entity.namespace, entity.deployment_name
        interrupted = entity.status == "upgrading"
    if interrupted or kubernetes.tracking_upgrade_required(namespace, deployment_name):
        logger.info("Automatically upgrading the existing MLflow tracking gateway")
        with session_factory() as session:
            installer.upgrade_mlflow_tracking(session)
        logger.info("MLflow tracking gateway migration completed")


async def _run() -> None:
    while True:
        try:
            await asyncio.to_thread(reconcile)
        except Exception:
            logger.exception("Automatic MLflow migration failed; retrying in 60 seconds")
        await asyncio.sleep(60)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run())


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
