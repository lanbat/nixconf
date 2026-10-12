"""Voiceprints: who an utterance sounds like.

A voiceprint is a few speaker embeddings of one person (unit vectors from the
CAM++ model, voice_id/embedder.py); an utterance is scored against each
person's centroid by cosine similarity, and every enrolled person gets a
score, so the assistant router can weigh them with who else is in the room
(assistant_router/people.py) instead of trusting a single best guess.

The store is voiceprints.json in a directory on the encrypted workload layer
(services/voice-id.nix): embeddings only, never audio. While the layer is
locked the directory is not there; then nobody is known and nothing is
learned or saved.

Unknown voices are never stored: utterances wait in memory (Recent) for ten
minutes, only so that "remember me" can enrol the person who just spoke.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile

VERSION = 1


def normalise_text(text: str) -> str:
    """A transcript as the router and this both see it: lower case, words only."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w' ]+", " ", (text or "").lower())).strip()


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else list(v)


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _centroid(vectors: list[list[float]]) -> list[float]:
    return _unit([sum(col) / len(vectors) for col in zip(*vectors)])


class Store:
    CAP = 40            # embeddings kept per person, oldest out
    MIN_ENROL = 3       # utterances needed to enrol someone
    DRIFT = 0.45        # a learned utterance must be at least this close

    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.path = os.path.join(directory, "voiceprints.json")
        self._prints: dict[str, list[list[float]]] = {}
        self._loaded_from: float | None = None
        self.reload()

    def available(self) -> bool:
        return os.path.isdir(self.directory) and os.access(self.directory, os.W_OK)

    def reload(self) -> None:
        self._prints = {}
        if not self.available() or not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        for key, vectors in (data.get("people") or {}).items():
            if isinstance(vectors, list) and vectors:
                self._prints[str(key)] = [_unit([float(x) for x in v]) for v in vectors]

    def _save(self) -> bool:
        if not self.available():
            return False
        # Voiceprints stay out of backups on purpose (re-enrolling takes a
        # minute): a cache tag makes `restic --exclude-caches` and the like
        # skip the directory if the workload layer is ever backed up whole.
        tag = os.path.join(self.directory, "CACHEDIR.TAG")
        if not os.path.exists(tag):
            with open(tag, "w", encoding="utf-8") as f:
                f.write("Signature: 8a477f597d28d172789f06886806bc55\n"
                        "# voice-id: voiceprints are not backed up (docs/backup.md).\n")
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".voiceprints.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "people": self._prints}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        return True

    def people(self) -> list[str]:
        return sorted(self._prints) if self.available() else []

    def scores(self, embedding: list[float]) -> dict[str, float]:
        """Cosine similarity to every enrolled person's centroid."""
        if not self.available():
            return {}
        e = _unit(embedding)
        return {key: round(_cos(e, _centroid(vs)), 4) for key, vs in self._prints.items()}

    def enrol(self, key: str, embeddings: list[list[float]]) -> int:
        """Start (or replace) a person's voiceprint; returns the utterances
        used, and keeps nothing unless there are at least MIN_ENROL."""
        if len(embeddings) < self.MIN_ENROL or not self.available():
            return len(embeddings)
        self._prints[key] = [_unit(e) for e in embeddings][-self.CAP:]
        self._save()
        return len(embeddings)

    def learn(self, key: str, embedding: list[float]) -> bool:
        """Add an utterance the router is sure was `key`'s; refused if it
        sounds too far from their voiceprint (no drift) or they aren't enrolled."""
        if not self.available() or key not in self._prints:
            return False
        e = _unit(embedding)
        if _cos(e, _centroid(self._prints[key])) < self.DRIFT:
            return False
        self._prints[key] = (self._prints[key] + [e])[-self.CAP:]
        self._save()
        return True

    def forget(self, key: str) -> bool:
        if key not in self._prints:
            return False
        del self._prints[key]
        self._save()
        return True


class Recent:
    """The last minutes of utterances, by transcript, in memory only."""

    def __init__(self, seconds: float = 600.0) -> None:
        self.seconds = seconds
        self._items: dict[str, tuple[float, list[float]]] = {}

    def add(self, text: str, embedding: list[float], now: float) -> None:
        self._items = {t: v for t, v in self._items.items() if now - v[0] <= self.seconds}
        key = normalise_text(text)
        if key:
            self._items[key] = (now, embedding)

    def get(self, text: str, now: float) -> list[float] | None:
        hit = self._items.get(normalise_text(text))
        return hit[1] if hit and now - hit[0] <= self.seconds else None

    def find(self, texts: list[str], now: float) -> list[list[float]]:
        return [e for e in (self.get(t, now) for t in texts) if e is not None]
