from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from threading import RLock
from typing import Any

from app.domain import now_iso, uid


@dataclass(frozen=True, slots=True)
class AuditEvent:
    entity_type: str
    entity_id: str
    action: str
    actor: str
    payload: dict[str, Any]
    previous_hash: str
    timestamp: str = field(default_factory=now_iso)
    id: str = field(default_factory=lambda: uid("evt"))
    event_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditLedger:
    """Append-only, hash-chained audit ledger for decision provenance."""

    def __init__(self, sink=None, initial_events: list[AuditEvent] | None = None) -> None:
        self._events: list[AuditEvent] = list(initial_events or [])
        self._lock = RLock()
        self._sink = sink

    def append(self, entity_type: str, entity_id: str, action: str, actor: str, payload: dict[str, Any]) -> AuditEvent:
        with self._lock:
            previous = self._events[-1].event_hash if self._events else "GENESIS"
            draft = AuditEvent(entity_type, entity_id, action, actor, payload, previous)
            canonical = json.dumps({k: v for k, v in draft.to_dict().items() if k != "event_hash"}, sort_keys=True, default=str)
            event = AuditEvent(**{**draft.to_dict(), "event_hash": hashlib.sha256(canonical.encode()).hexdigest()})
            self._events.append(event)
            if self._sink:
                self._sink(event)
            return event

    def list(self, entity_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        events = self._events if entity_id is None else [e for e in self._events if e.entity_id == entity_id]
        return [event.to_dict() for event in events[-limit:]]

    def verify(self) -> bool:
        previous = "GENESIS"
        for event in self._events:
            if event.previous_hash != previous:
                return False
            canonical = json.dumps({k: v for k, v in event.to_dict().items() if k != "event_hash"}, sort_keys=True, default=str)
            if hashlib.sha256(canonical.encode()).hexdigest() != event.event_hash:
                return False
            previous = event.event_hash
        return True
