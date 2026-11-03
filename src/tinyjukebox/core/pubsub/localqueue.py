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
        self._message_queue: queue.Queue[_Item] = queue.Queue()  # a new one with every connection
        self._worker: LoggerThread | None = None  # the worker of the connection
        # all running workers: a handler may disconnect - its worker then runs until the handler returns. An own
        # lock: a worker must never wait for _lock_client
        self._workers: set[threading.Thread] = set()
        self._workers_lock = threading.Lock()
        self._local = threading.local()  # published_at of the message a worker dispatches

    def _work(self, messages: "queue.Queue[_Item]") -> None:
        try:
            while (item := messages.get()) is not None:
                topic, payload, self._local.published_at = item
                # nothing after a disconnect: neither while the client is disconnected nor once it has connected again
                if messages is self._message_queue and self.is_connected.is_set():
                    self._dispatch(topic, payload)
                self._local.published_at = None
                messages.task_done()
            messages.task_done()
        finally:
            with self._workers_lock:
                self._workers.discard(threading.current_thread())

    def connect(self) -> None:
        with self._lock_client:
            if self.is_connected.is_set():
                self._logger.info("connect - already connected.")
                return
            self._message_queue = queue.Queue()
            worker = LoggerThread(
                target=self._work, args=(self._message_queue,), name="LocalQueue", logger=self._logger, daemon=True
            )
            self._worker = worker
            with self._workers_lock:
                self._workers.add(worker)
            worker.start()
            self.is_disconnected.clear()
            self.is_connected.set()

    def disconnect(self) -> None:
        with self._lock_client:
            worker = self._worker
            if worker is None:
                return
            self.is_connected.clear()
            self._message_queue.put(None)
            self._worker = None
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
        if self._is_worker():
            return
        self._message_queue.join()

    def published_at(self) -> float | None:
        return getattr(self._local, "published_at", None)

    def _is_worker(self) -> bool:
        with self._workers_lock:
            return threading.current_thread() in self._workers

    def _activate_subscription(self, topic: str) -> None:
        pass

    def _deactivate_subscriptions(self, topics: list[str]) -> None:
        pass
