from __future__ import annotations

import json
import logging
from typing import Any, Optional

import pika

from app.config import Settings, get_settings
from app.services.rabbitmq.connection import connect, declare_topology

logger = logging.getLogger(__name__)


class BatchJobPublisher:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    def publish_jobs(self, messages: list[dict[str, Any]]) -> int:
        if not messages:
            return 0
        connection = connect(self.settings)
        try:
            channel = connection.channel()
            queue = declare_topology(channel, self.settings)
            channel.confirm_delivery()
            published = 0
            for message in messages:
                body = json.dumps(message, default=str).encode("utf-8")
                channel.basic_publish(
                    exchange="",
                    routing_key=queue,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        content_type="application/json",
                    ),
                )
                published += 1
            logger.info("Published %s jobs to queue=%s", published, queue)
            return published
        finally:
            connection.close()
