from __future__ import annotations

import logging
from typing import Optional

import pika
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
from pika.exceptions import ChannelClosedByBroker

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def build_connection_parameters(settings: Optional[Settings] = None) -> pika.ConnectionParameters:
    cfg = settings or get_settings()
    credentials = pika.PlainCredentials(cfg.rabbitmq_username, cfg.rabbitmq_password)
    return pika.ConnectionParameters(
        host=cfg.rabbitmq_host,
        port=cfg.rabbitmq_port,
        virtual_host=cfg.rabbitmq_virtual_host,
        credentials=credentials,
        # OCR can take several minutes; low heartbeat causes ConnectionReset mid-job
        heartbeat=600,
        blocked_connection_timeout=600,
        connection_attempts=3,
        retry_delay=2,
    )


def connect(settings: Optional[Settings] = None) -> BlockingConnection:
    params = build_connection_parameters(settings)
    logger.info(
        "Connecting RabbitMQ %s:%s vhost=%s",
        params.host,
        params.port,
        params.virtual_host,
    )
    return pika.BlockingConnection(params)


def declare_topology(
    channel: BlockingChannel,
    settings: Optional[Settings] = None,
) -> str:
    """
    Mirror .NET consumer topology:

      QueueDeclare(main, durable, args: x-dead-letter-exchange + routing-key)
      ExchangeDeclare(DLX, Direct)
      QueueDeclare(failed)
      QueueBind(failed, DLX, failedRoutingKey)
    """
    cfg = settings or get_settings()
    queue = cfg.rabbitmq_queue_verify
    failed_queue = cfg.rabbitmq_queue_failed
    dlx = cfg.rabbitmq_dead_exchange

    # Dead-letter exchange + failed queue (like FailedInsertTxQueue)
    channel.exchange_declare(exchange=dlx, exchange_type="direct", durable=True)
    channel.queue_declare(
        queue=failed_queue,
        durable=True,
        exclusive=False,
        auto_delete=False,
        arguments=None,
    )
    channel.queue_bind(queue=failed_queue, exchange=dlx, routing_key=failed_queue)

    arguments = {
        "x-dead-letter-exchange": dlx,
        "x-dead-letter-routing-key": failed_queue,
    }
    try:
        channel.queue_declare(
            queue=queue,
            durable=True,
            exclusive=False,
            auto_delete=False,
            arguments=arguments,
        )
    except ChannelClosedByBroker as exc:
        # Queue already exists without DLX args — RabbitMQ rejects redeclare
        logger.error(
            "Queue %s exists with different args (need DLX). "
            "Delete it in RabbitMQ management (vhost=%s) then restart. Error: %s",
            queue,
            cfg.rabbitmq_virtual_host,
            exc,
        )
        raise

    logger.info(
        "RabbitMQ topology ready queue=%s failed=%s dlx=%s",
        queue,
        failed_queue,
        dlx,
    )
    return queue


def declare_verify_queue(
    channel: BlockingChannel,
    settings: Optional[Settings] = None,
) -> str:
    """Backward-compatible alias."""
    return declare_topology(channel, settings)


def get_queue_message_count(settings: Optional[Settings] = None) -> int:
    """Ready messages on main verify queue. Returns 0 if unreachable."""
    cfg = settings or get_settings()
    try:
        connection = connect(cfg)
        try:
            channel = connection.channel()
            result = channel.queue_declare(
                queue=cfg.rabbitmq_queue_verify,
                durable=True,
                exclusive=False,
                auto_delete=False,
                arguments={
                    "x-dead-letter-exchange": cfg.rabbitmq_dead_exchange,
                    "x-dead-letter-routing-key": cfg.rabbitmq_queue_failed,
                },
            )
            return int(result.method.message_count or 0)
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read RabbitMQ queue depth: %s", exc)
        return 0


def get_failed_queue_message_count(settings: Optional[Settings] = None) -> int:
    cfg = settings or get_settings()
    try:
        connection = connect(cfg)
        try:
            channel = connection.channel()
            result = channel.queue_declare(
                queue=cfg.rabbitmq_queue_failed,
                durable=True,
                exclusive=False,
                auto_delete=False,
            )
            return int(result.method.message_count or 0)
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read failed queue depth: %s", exc)
        return 0
