"""Message counters per topic."""

import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, NamedTuple

__all__ = ["StatEntry", "Statistics", "Totals"]


@dataclass
class StatEntry:
    """Counter for sent and received messages of one topic."""

    sent_messages: int = 0
    received_messages: int = 0


class Totals(NamedTuple):
    received_messages: int
    sent_messages: int


class Statistics:
    """Thread-safe sent/received counters for all topics."""

    def __init__(self) -> None:
        self.stats: defaultdict[str, StatEntry] = defaultdict(StatEntry)
        self._lock = threading.Lock()

    def sent(self, topic: str) -> None:
        """Increase the sent message counter of ``topic``."""
        with self._lock:
            self.stats[topic].sent_messages += 1

    def recv(self, topic: str) -> None:
        """Increase the received message counter of ``topic``."""
        with self._lock:
            self.stats[topic].received_messages += 1

    def get_totals(self) -> Totals:
        """Sum of all counters."""
        with self._lock:
            return Totals(
                received_messages=sum(s.received_messages for s in self.stats.values()),
                sent_messages=sum(s.sent_messages for s in self.stats.values()),
            )

    def get_stats(self) -> dict[str, Any]:
        """Totals and per-topic counters as a JSON-serializable dict."""
        received, sent = self.get_totals()
        with self._lock:
            topics = {
                topic: {"messages-received": s.received_messages, "messages-sent": s.sent_messages}
                for topic, s in self.stats.items()
            }
        return {"received": received, "sent": sent, "topics": topics}
