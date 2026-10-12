"""In-process pubsub client."""

import logging
import queue
import threading
import time

from tinyjukebox.core.pubsub.base import NotConnectedError, Payload, PubSubClient
from tinyjukebox.core.threads import LoggerThread

__all__ = ["LocalQueue"]

_Item = tuple[str, Payload, float] | None  # topic, payload, time.monotonic() of the publication


class LocalQueue(PubSubClient):
    """Pubsub client that delivers messages within the process via a ``queue.Queue``.

    Messages are delivered asynchronously and in order by a single worker thread - handlers must therefore never
    block. Handlers receive the published object itself (no copy).
    """

    def __init__(self, logger: logging.Logger) -> None:
        super().__init__(logger, "LocalQueue")
        self._message_queue: queue.Queue[_Item] = queue.Queue()
        self._worker: LoggerThread | None = None
        self._published_at: float | None = None  # of the message being dispatched (only used by the worker)

    def _work(self) -> None:
        while (item := self._message_queue.get()) is not None:
            topic, payload, self._published_at = item
            if self.is_connected.is_set():
                self._dispatch(topic, payload)
            self._published_at = None
            self._message_queue.task_done()
        self._message_queue.task_done()

    def connect(self) -> None:
        with self._lock_client:
            if self.is_connected.is_set():
                self._logger.info("connect - already connected.")
                return
            self._worker = LoggerThread(target=self._work, name="LocalQueue", logger=self._logger, daemon=True)
            self._worker.start()
            self.is_disconnected.clear()
            self.is_connected.set()

    def disconnect(self) -> None:
        with self._lock_client:
            if self._worker is None:
                return
            self.is_connected.clear()
            self._message_queue.put(None)
            worker, self._worker = self._worker, None
        if worker is not threading.current_thread():  # disconnect might be called by a message handler
            worker.join()
        self.is_disconnected.set()

    def publish(self, topic: str, msg: Payload) -> None:
        self._logger.debug("publish - topic '%s', message '%s'.", topic, msg)
        if not self.is_connected.is_set():
            raise NotConnectedError("publish - trying to publish while not being connected.")
        self.stats.sent(topic)
        self._message_queue.put((topic, msg, time.monotonic()))

    def wait_until_delivered(self) -> None:
        """Block until all published messages have been delivered to their handlers.

        Returns immediately if called by a message handler - the worker would wait for itself.
        """
        if self._worker is threading.current_thread():
            return
        self._message_queue.join()

    def published_at(self) -> float | None:
        return self._published_at if self._worker is threading.current_thread() else None

    def _activate_subscription(self, topic: str) -> None:
        pass

    def _deactivate_subscriptions(self, topics: list[str]) -> None:
        pass
