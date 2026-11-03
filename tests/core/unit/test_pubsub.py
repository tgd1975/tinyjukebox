"""Behavior shared by all pubsub clients (tested with the synchronous test double and LocalQueue)."""

import logging
import threading
import time

import pytest

from tests.support import SyncPubSub, wait_for
from tinyjukebox.core.pubsub import LocalQueue, NotConnectedError
from tinyjukebox.core.pubsub.statistics import Statistics


class TestSubscriptions:
    def test_handlers_receive_the_payload_object(self, sync_pubsub: SyncPubSub) -> None:
        received: list[object] = []
        payload = {"a": 1}
        sync_pubsub.subscribe("/a", received.append)
        sync_pubsub.publish("/a", "text")
        sync_pubsub.publish("/a", payload)
        sync_pubsub.publish("/a", None)
        assert received == ["text", payload, None]
        assert received[1] is payload

    def test_several_handlers_per_topic(self, sync_pubsub: SyncPubSub) -> None:
        a: list[object] = []
        b: list[object] = []
        sync_pubsub.subscribe("/t", a.append)
        sync_pubsub.subscribe("/t", b.append)
        sync_pubsub.publish("/t", "x")
        assert a == b == ["x"]

    def test_duplicate_subscription(self, sync_pubsub: SyncPubSub) -> None:
        received: list[object] = []
        sync_pubsub.subscribe("/t", received.append)
        with pytest.raises(ValueError, match="already added"):
            sync_pubsub.subscribe("/t", received.append)
        sync_pubsub.subscribe("/t", received.append, ignore_duplicate=True)
        sync_pubsub.publish("/t", "x")
        assert received == ["x"]  # handler registered once only

    def test_unsubscribe(self, sync_pubsub: SyncPubSub) -> None:
        a: list[object] = []
        b: list[object] = []
        sync_pubsub.subscribe("/t", a.append)
        sync_pubsub.subscribe("/t", b.append)
        sync_pubsub.unsubscribe("/t", a.append)
        sync_pubsub.publish("/t", "x")
        assert (a, b) == ([], ["x"])
        sync_pubsub.unsubscribe("/t", b.append)
        assert sync_pubsub.subscribed_topics() == {}

    def test_unsubscribe_unknown(self, sync_pubsub: SyncPubSub) -> None:
        with pytest.raises(ValueError, match="unknown"):
            sync_pubsub.unsubscribe("/t", print)
        sync_pubsub.subscribe("/t", repr)
        with pytest.raises(ValueError, match="unknown"):
            sync_pubsub.unsubscribe("/t", print)
        sync_pubsub.unsubscribe("/t", print, ignore_not_found=True)
        sync_pubsub.unsubscribe("/other", print, ignore_not_found=True)
        assert sync_pubsub.subscribed_topics() == {"/t": ["repr"]}

    def test_unsubscribe_all(self, sync_pubsub: SyncPubSub) -> None:
        sync_pubsub.subscribe("/a", repr)
        sync_pubsub.subscribe("/", repr)
        sync_pubsub.unsubscribe_all()
        assert sync_pubsub.subscribed_topics() == {}

    @pytest.mark.parametrize(
        ("subscription", "topic", "matches"),
        [("/a/+/c", "/a/b/c", True), ("/a/#", "/a/b/c", True), ("/a/+", "/a/b/c", False), ("/x/#", "/a", False)],
    )
    def test_wildcards(self, sync_pubsub: SyncPubSub, subscription: str, topic: str, matches: bool) -> None:
        received: list[object] = []
        sync_pubsub.subscribe(subscription, received.append)
        sync_pubsub.publish(topic, "x")
        assert received == (["x"] if matches else [])

    def test_handler_on_exact_and_wildcard_topic_is_called_once(self, sync_pubsub: SyncPubSub) -> None:
        received: list[object] = []
        sync_pubsub.subscribe("/a/", received.append)
        sync_pubsub.subscribe("/a/#", received.append)
        sync_pubsub.publish("/a/", "x")
        assert received == ["x"]

    def test_failing_handler_does_not_stop_dispatch(
        self, sync_pubsub: SyncPubSub, caplog: pytest.LogCaptureFixture
    ) -> None:
        received: list[object] = []

        def fail(message: bytes) -> None:
            raise RuntimeError("handler failed")

        sync_pubsub.subscribe("/t", fail)
        sync_pubsub.subscribe("/t", received.append)
        sync_pubsub.publish("/t", "x")
        assert received == ["x"]
        assert "handler failed" in caplog.text

    def test_get_stats(self, sync_pubsub: SyncPubSub) -> None:
        sync_pubsub.subscribe("/t", repr)
        sync_pubsub.publish("/t", "x")
        sync_pubsub.publish("/u", "x")
        stats = sync_pubsub.get_stats()
        assert stats["connected"] is True
        assert stats["subscribed_topics"] == {"/t": ["repr"]}
        assert stats["message_stats"] == {
            "received": 2,
            "sent": 2,
            "topics": {
                "/t": {"messages-received": 1, "messages-sent": 1},
                "/u": {"messages-received": 1, "messages-sent": 1},
            },
        }


class TestStatistics:
    def test_totals(self) -> None:
        stats = Statistics()
        stats.sent("/a")
        stats.sent("/a")
        stats.recv("/")
        assert stats.get_totals() == (1, 2)
        assert stats.get_totals().sent_messages == 2


class TestLocalQueue:
    def test_publish_requires_connection(self, logger: logging.Logger) -> None:
        client = LocalQueue(logger)
        with pytest.raises(NotConnectedError):
            client.publish("/t", "x")

    def test_delivery(self, local_queue: LocalQueue) -> None:
        received: list[object] = []
        local_queue.subscribe("/t", received.append)
        for i in range(5):
            local_queue.publish("/t", i)
        local_queue.wait_until_delivered()
        assert received == [0, 1, 2, 3, 4]

    def test_reconnect(self, logger: logging.Logger) -> None:
        client = LocalQueue(logger)
        received: list[object] = []
        client.subscribe("/t", received.append)
        for _ in range(3):
            client.connect()
            client.connect()  # second connect is a no-op
            assert client.is_connected.is_set()
            client.publish("/t", "x")
            client.wait_until_delivered()
            client.disconnect()
            client.disconnect()  # second disconnect is a no-op
            assert client.is_disconnected.is_set()
        assert received == ["x"] * 3

    def test_published_at_is_known_in_the_handler(self, local_queue: LocalQueue) -> None:
        seen: list[float | None] = []
        local_queue.subscribe("/t", lambda message: seen.append(local_queue.published_at()))
        before = time.monotonic()
        local_queue.publish("/t", "x")
        local_queue.wait_until_delivered()
        assert seen[0] is not None
        assert before <= seen[0] <= time.monotonic()
        assert local_queue.published_at() is None  # outside of a handler

    def test_wait_until_delivered_from_handler_returns(self, local_queue: LocalQueue) -> None:
        done = threading.Event()

        def handler(message: bytes) -> None:
            local_queue.wait_until_delivered()  # would wait for itself
            done.set()

        local_queue.subscribe("/t", handler)
        local_queue.publish("/t", "x")
        assert done.wait(5)

    def test_disconnect_from_handler(self, local_queue: LocalQueue) -> None:
        done = threading.Event()

        def handler(message: bytes) -> None:
            local_queue.disconnect()
            done.set()

        local_queue.subscribe("/t", handler)
        local_queue.publish("/t", "x")
        assert done.wait(5)
        assert wait_for(local_queue.is_disconnected.is_set)


def test_a_failing_listener_of_unexpected_exceptions_ends_no_dispatch(logger):
    """The listener of the box writes the journal: on a read-only card it fails - the dispatch goes on."""
    from tinyjukebox.core import unexpected

    queue = LocalQueue(logger)
    delivered: list[object] = []

    def failing_handler(message):
        raise RuntimeError("a handler with a bug")

    def failing_listener(error, where):
        raise OSError(30, "Read-only file system")

    queue.subscribe("bad", failing_handler)
    queue.subscribe("good", delivered.append)
    unexpected.subscribe(failing_listener)
    queue.connect()
    try:
        queue.publish("bad", 1)
        queue.publish("good", 2)
        queue.wait_until_delivered()
    finally:
        unexpected.unsubscribe(failing_listener)
        queue.disconnect()
    assert delivered == [2]
