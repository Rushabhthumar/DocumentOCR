from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from app.auth_context import clear_request_auth, set_request_auth
from app.config import Settings, get_settings
from app.models.verification_models import VerificationDecision
from app.services.auth_token_store import auth_token_store
from app.services.batch_job_tracker import batch_job_tracker
from app.services.batch_pause_controller import batch_pause_controller
from app.services.network_errors import AuthExpiredError, TransientNetworkError
from pika.exceptions import AMQPConnectionError, AMQPError, ChannelWrongStateError, StreamLostError

from app.services.rabbitmq.connection import connect, declare_topology
from app.services.token_renew_service import token_renew_service
from app.services.transaction_verification_service import TransactionVerificationService

logger = logging.getLogger(__name__)


def _is_amqp_connection_error(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (StreamLostError, ChannelWrongStateError, AMQPConnectionError, ConnectionResetError),
    ) or (
        isinstance(exc, AMQPError)
        and ("connection" in str(exc).lower() or "stream" in str(exc).lower())
    )

# PaddleOCR / native libs are not multi-thread safe — serialize verify() in-process.
_verify_lock = threading.Lock()


class BatchVerifyConsumer:
    """
    RabbitMQ consumer aligned with .NET ExecuteAsync pattern:

      - Declare main queue with DLX args
      - Declare dead exchange + failed queue + bind
      - BasicConsume(autoAck=false)
      - success  -> BasicAck
      - hard fail -> BasicNack(requeue=false)  # routes to failed queue via DLX
      - transient DNS/401 -> BasicNack(requeue=true)  # retry (OCR-specific)
    """

    def __init__(
        self,
        *,
        worker_id: int,
        settings: Optional[Settings] = None,
        stop_event: Optional[threading.Event] = None,
    ):
        self.worker_id = worker_id
        self.settings = settings or get_settings()
        self.stop_event = stop_event or threading.Event()
        self.verification_service = TransactionVerificationService(self.settings)

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._consume_until_error()
            except Exception as exc:  # noqa: BLE001
                if self.stop_event.is_set():
                    break
                logger.exception(
                    "Worker %s RabbitMQ loop error: %s — reconnect in 5s",
                    self.worker_id,
                    exc,
                )
                time.sleep(5)

    def _consume_until_error(self) -> None:
        connection = connect(self.settings)
        channel = connection.channel()
        queue = declare_topology(channel, self.settings)
        channel.basic_qos(prefetch_count=max(1, int(self.settings.rabbitmq_prefetch)))

        logger.info(
            "Worker %s consuming queue=%s (failed=%s dlx=%s)",
            self.worker_id,
            queue,
            self.settings.rabbitmq_queue_failed,
            self.settings.rabbitmq_dead_exchange,
        )

        def on_message(ch, method, _properties, body: bytes) -> None:
            # Same shape as .NET: Received handler with try/ack or nack
            if self.stop_event.is_set():
                ch.basic_nack(delivery_tag=method.delivery_tag, multiple=False, requeue=True)
                ch.stop_consuming()
                return

            batch_pause_controller.wait_if_paused()
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(body.decode("utf-8"))
                self._process(payload)
                # Success path (.NET BasicAck) — message fully handled
                ch.basic_ack(delivery_tag=method.delivery_tag, multiple=False)
            except (TransientNetworkError, AuthExpiredError) as exc:
                # Retryable — not sent to DLX (requeue=true)
                logger.warning(
                    "Worker %s transient/auth for %s: %s — requeue",
                    self.worker_id,
                    payload.get("transaction_id"),
                    exc,
                )
                if isinstance(exc, AuthExpiredError):
                    try:
                        token_renew_service.pause_renew_resume(reason="worker_401")
                    except Exception:  # noqa: BLE001
                        logger.exception("Token renew failed after worker 401")
                time.sleep(2)
                try:
                    ch.basic_nack(
                        delivery_tag=method.delivery_tag,
                        multiple=False,
                        requeue=True,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("Worker %s could not nack after transient error", self.worker_id)
                    raise
            except Exception as exc:  # noqa: BLE001
                # RabbitMQ dropped during long OCR — reconnect + requeue, do NOT mark EXTRACTION_FAILED
                if _is_amqp_connection_error(exc):
                    logger.warning(
                        "Worker %s AMQP lost during %s: %s — reconnect/requeue",
                        self.worker_id,
                        payload.get("transaction_id"),
                        exc,
                    )
                    try:
                        ch.basic_nack(
                            delivery_tag=method.delivery_tag,
                            multiple=False,
                            requeue=True,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    raise

                # Hard fail (.NET BasicNack requeue=false) -> DLX failed queue
                logger.exception(
                    "Worker %s failed txn %s — nack to dead-letter/failed queue",
                    self.worker_id,
                    payload.get("transaction_id"),
                )
                job_id = str(payload.get("job_id") or "")
                txn_id = str(payload.get("transaction_id") or "")
                if job_id and txn_id:
                    batch_job_tracker.record_item(
                        job_id=job_id,
                        transaction_id=txn_id,
                        status="FAILED",
                        decision=VerificationDecision.EXTRACTION_FAILED.value,
                        message="Worker processing error — sent to failed queue",
                        errors=[str(exc)],
                    )
                try:
                    ch.basic_nack(
                        delivery_tag=method.delivery_tag,
                        multiple=False,
                        requeue=False,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("Worker %s could not nack to DLX (channel already closed)", self.worker_id)
                    raise

        channel.basic_consume(queue=queue, on_message_callback=on_message, auto_ack=False)
        try:
            while not self.stop_event.is_set():
                connection.process_data_events(time_limit=1)
        finally:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass

    def _process(self, payload: dict[str, Any]) -> None:
        job_id = str(payload.get("job_id") or "")
        txn_id = str(payload.get("transaction_id") or "")
        force = bool(payload.get("force", True))
        raw_txn = payload.get("transaction")
        # Empty dict must not skip Finance GetTransaction (no attachments → false DOCUMENT_NOT_FOUND).
        transaction_payload = raw_txn if isinstance(raw_txn, dict) and raw_txn else None

        if not txn_id:
            raise ValueError("Queue message missing transaction_id")

        snap = auth_token_store.snapshot()
        set_request_auth(snap.get("authorization"), snap.get("app_id"), snap.get("api_key"))
        try:
            if job_id:
                batch_job_tracker.mark_running(job_id)
            with _verify_lock:
                result = self.verification_service.verify(
                    txn_id,
                    force=force,
                    transaction_payload=transaction_payload,
                )
            if result.decision == VerificationDecision.VERIFIED:
                status = "COMPLETED"
            else:
                status = "FAILED"
            errors = list(result.errors or [])
            if not errors and result.message and status == "FAILED":
                errors = [result.message]
            if job_id:
                batch_job_tracker.record_item(
                    job_id=job_id,
                    transaction_id=txn_id,
                    status=status,
                    decision=result.decision.value,
                    message=result.message or "",
                    errors=errors,
                )
            logger.info(
                "Worker %s finished %s decision=%s",
                self.worker_id,
                txn_id,
                result.decision.value,
            )
        finally:
            clear_request_auth()


class RabbitMQWorkerManager:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if not self.settings.batch_use_rabbitmq:
            logger.info("RabbitMQ workers not started (BATCH_USE_RABBITMQ=false)")
            return
        count = max(1, int(self.settings.rabbitmq_worker_count))
        self._stop.clear()
        for i in range(count):
            consumer = BatchVerifyConsumer(
                worker_id=i + 1,
                settings=self.settings,
                stop_event=self._stop,
            )
            thread = threading.Thread(
                target=consumer.run_forever,
                name=f"ocr-rmq-worker-{i + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        logger.info("Started %s RabbitMQ verification workers", count)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads.clear()
        logger.info("RabbitMQ workers stopped")


rabbitmq_worker_manager = RabbitMQWorkerManager()
