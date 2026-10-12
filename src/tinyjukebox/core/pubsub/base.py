"""Common base class of all pubsub clients."""

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from tinyjukebox.core import unexpected
from tinyjukebox.core.logging import get_child
from tinyjukebox.core.pubsub.statistics import Statistics

__all__ = ["MessageHandler", "NotConnectedError", "Payload", "PubSubClient", "PubSubError"]

Payload = object
"""Any (immutable) Python object - payloads are passed to the handlers unchanged (see :mod:`tinyjukebox.messages`)."""

MessageHandler = Callable[[Any], object]
"""A message handler receives the payload of a message."""


class PubSubError(RuntimeError):
    """Base class for all errors raised by pubsub clients."""


class NotConnectedError(PubSubError):
    """Raised if a message should be published while the client is not connected."""


class PubSubClient(ABC):
    """Topic/handler registry shared by all pubsub implementations.

    Any number of handlers can be registered per topic; every handler receives the payload of each
    message published to a matching topic (wildcards: ``+`` one level, ``#`` all remaining levels). Subscriptions
    are independent of the connection state: they can be added before :meth:`connect` and are re-activated on every
    (re)connect.

    ``is_connected`` and ``is_disconnected`` are ``threading.Event`` instances that reflect the connection state.
    """

    def __init__(self, logger: logging.Logger, logger_name: str) -> None:
        self._logger = get_child(logger, logger_name)
        self._logger.info("__init__ - initializing")
        self.is_connected = threading.Event()
        self.is_disconnected = threading.Event()
        self.is_disconnected.set()
        # _lock_client serializes (dis)connect/(un)subscribe.
        # _handlers_lock guards the registry only and is held briefly; the thread dispatching messages must never
        # wait for _lock_client (dead-lock while e.g. disconnect() waits for the dispatch thread).
        self._lock_client = threading.Lock()
        self._handlers_lock = threading.Lock()
        self._topic_handler: dict[str, list[MessageHandler]] = {}
        self.stats = Statistics()

    @property
    def logger(self) -> logging.Logger:
        """The logger of this client."""
        return self._logger

    @abstractmethod
    def connect(self) -> None:
        """Connect and activate all registered subscriptions."""

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect; the registered subscriptions are kept."""

    @abstractmethod
    def publish(self, topic: str, msg: Payload) -> None:
        """Publish ``msg`` to ``topic``.

        :raises NotConnectedError: if the client is not connected.
        """

    def published_at(self) -> float | None:
        """``time.monotonic()`` of the publication of the message that is currently being dispatched (to be called
        by message handlers); ``None`` if the client does not know it."""
        return None

    @abstractmethod
    def _activate_subscription(self, topic: str) -> None:
        """Called (with ``_lock_client`` held) after the first handler for ``topic`` has been registered.

        Implementations must not access the handler registry directly.
        """

    @abstractmethod
    def _deactivate_subscriptions(self, topics: list[str]) -> None:
        """Called (with ``_lock_client`` held) after the last handler of each of ``topics`` has been removed."""

    def subscribe(self, topic: str, handler: MessageHandler, ignore_duplicate: bool = False) -> None:
        """Register ``handler`` for ``topic``.

        :param ignore_duplicate: if false, registering the same topic/handler pair twice raises a ``ValueError``;
            if true, the second registration is silently ignored.
        """
        self._logger.info("subscribe - topic '%s', handler '%s'.", topic, _handler_name(handler))
        with self._lock_client:
            with self._handlers_lock:
                handlers = self._topic_handler.get(topic)
                if handlers is not None:
                    if handler not in handlers:
                        handlers.append(handler)
                    elif not ignore_duplicate:
                        raise ValueError(f"subscribe - topic/handler pair already added. ({topic}/{handler})")
                    return
                self._topic_handler[topic] = [handler]
            self._activate_subscription(topic)

    def unsubscribe(self, topic: str, handler: MessageHandler, ignore_not_found: bool = False) -> None:
        """Remove a topic/handler pair (the reverse of :meth:`subscribe`).

        Other handlers of the same topic stay registered. The topic itself is unsubscribed as soon as no handler
        is left.

        :param ignore_not_found: if false, unknown topic/handler pairs raise a ``ValueError``.
        """
        self._logger.info("unsubscribe - topic '%s', handler '%s'.", topic, _handler_name(handler))
        with self._lock_client:
            with self._handlers_lock:
                handlers = self._topic_handler.get(topic)
                if handlers is None or handler not in handlers:
                    if ignore_not_found:
                        return
                    raise ValueError(f"unsubscribe - unknown topic/handler pair ({topic}/{handler}).")
                handlers.remove(handler)
                if handlers:
                    return
                del self._topic_handler[topic]
            self._deactivate_subscriptions([topic])

    def unsubscribe_all(self) -> None:
        """Remove all subscriptions."""
        with self._lock_client:
            with self._handlers_lock:
                topics = list(self._topic_handler)
                self._topic_handler.clear()
            self._logger.info("unsubscribe_all - topics %s", topics)
            if topics:
                self._deactivate_subscriptions(topics)

    def subscribed_topics(self) -> dict[str, list[str]]:
        """All subscribed topics with the (qualified) names of their handlers."""
        with self._handlers_lock:
            return {topic: [_handler_name(h) for h in handlers] for topic, handlers in self._topic_handler.items()}

    def _topics(self) -> list[str]:
        """All subscribed topics (snapshot)."""
        with self._handlers_lock:
            return list(self._topic_handler)

    def get_stats(self) -> dict[str, Any]:
        """Connection state, message statistics and subscriptions."""
        return {
            "connected": self.is_connected.is_set(),
            "message_stats": self.stats.get_stats(),
            "subscribed_topics": self.subscribed_topics(),
        }

    def _handlers_for(self, topic: str) -> list[MessageHandler]:
        """All handlers whose subscription matches ``topic`` (incl. wildcard subscriptions)."""
        with self._handlers_lock:
            handlers = list(self._topic_handler.get(topic, ()))
            for subscription, subscription_handlers in self._topic_handler.items():
                if subscription != topic and _is_wildcard(subscription) and topic_matches(subscription, topic):
                    handlers.extend(h for h in subscription_handlers if h not in handlers)
            return handlers

    def _dispatch(self, topic: str, payload: Payload) -> None:
        """Call all handlers registered for ``topic``. Exceptions of handlers are logged and swallowed."""
        self.stats.recv(topic)
        for handler in self._handlers_for(topic):
            try:
                handler(payload)
            except Exception as error:
                self._logger.exception("message handler '%s' for topic '%s' failed.", _handler_name(handler), topic)
                unexpected.report(error, f"handler {_handler_name(handler)} of the topic {topic}")


def topic_matches(subscription: str, topic: str) -> bool:
    """True if ``topic`` matches ``subscription`` (``+``: exactly one level, ``#``: all remaining levels, also none)."""
    levels = topic.split("/")
    for index, part in enumerate(subscription.split("/")):
        if part == "#":
            return True
        if index >= len(levels) or part not in ("+", levels[index]):
            return False
    return len(subscription.split("/")) == len(levels)


def _is_wildcard(topic: str) -> bool:
    return "+" in topic or "#" in topic


def _handler_name(handler: MessageHandler) -> str:
    return getattr(handler, "__qualname__", repr(handler))
