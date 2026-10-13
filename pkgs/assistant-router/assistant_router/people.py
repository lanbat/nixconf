"""Who the assistant is talking to: a hint from voice, faces and phones.

Evidence arrives as (room, source, person key, confidence). The person keys
are the profile's people (lanbat.deployment.people, and those enrolled since);
anything else is dropped, so no free text from a satellite or a robot reaches
a prompt. The sources:

- "phone": a person's phone heard in the room (pkgs/room-presence), last 2 min;
  heard by sensors in several rooms (through walls), it counts only where it
  is loudest, or in each room within ROOM_MARGIN_DB of that;
- "face": a face recognised in the room (cameras via Frigate), last 10 s;
- "aimed": the face the room's robot saw after turning to the voice, last 10 s;
- "card": a person's NFC card tapped on the room's robot (cards.py),
  last 2 min: strong, it names them unless the voice clearly disagrees;
- "tap": a person's phone held to the room's robot, and their login on its
  page (tap.py), last 2 min: as strong as a card;
- home: a person's phone on the home network (pkgs/wifi-presence), for the
  whole house, last 3 min;
- and, per request, the voice: a score for every enrolled person (voice-id).

Rather than eliminate people outright, each enrolled person is a candidate
with a weight: everyone starts small, presence raises it (a phone, a face in
the room, having been the speaker earlier in this conversation), absence
never rules anyone out. The voice scores and an aimed face multiply in as
evidence, and an extra candidate stands for someone not enrolled. A speaker
is named only when one candidate clearly leads (POSTERIOR_MIN, LEAD_MIN);
otherwise nobody is: unknown beats wrong. So presence narrows who a voice can
be, without a phone left in another room overruling a clear voice.

However sure, it is a hint for what the assistant says, never authority over
anything, and the line says so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

SOURCES = ("face", "aimed", "phone", "card", "tap")
WINDOW = {"face": 10.0, "aimed": 10.0, "phone": 120.0, "card": 120.0, "tap": 120.0}
MIN_CONFIDENCE = {"face": 0.6, "aimed": 0.6, "phone": 0.5, "card": 0.5, "tap": 0.5}

# The weights. Tuned on the scenarios in tests/test_people.py.
PRIOR = 0.05            # every enrolled person
PRIOR_UNKNOWN = 0.1     # someone not enrolled
PHONE = 4.0             # × prior: their phone is in the room
HOME = 2.0              # × prior: their phone is on the home network
CARD = 100.0            # × prior: they just tapped their card, or their phone, on the robot
HOME_WINDOW = 180.0     # the house sensor repeats its set every minute
ROOM_MARGIN_DB = 6      # a phone this much quieter than in its loudest room isn't there
FACE = 6.0              # × prior: their face is in the room
CONTINUITY = 5.0        # × prior: they were the speaker earlier in this conversation
VOICE_SHARPNESS = 10.0  # likelihood exp(k·cosine)
VOICE_UNKNOWN = 0.25    # the cosine an unenrolled voice scores against itself
AIMED = 1200.0          # × (1 + AIMED·confidence): the face the robot turned to
POSTERIOR_MIN = 0.8
LEAD_MIN = 3.0          # × the runner-up (the unknown candidate included)
LEARN_VOICE = 0.8       # a voice this close, and LEARN_MARGIN ahead, teaches itself
LEARN_MARGIN = 0.2
LEARN_AIMED = 0.8       # an aimed face this sure teaches the voice


@dataclass
class Evidence:
    person: str
    confidence: float
    at: float


@dataclass
class Assessment:
    speaker: str | None = None
    posterior: float = 0.0
    how: str = ""
    nearby: list[str] = field(default_factory=list)
    learn: str | None = None  # whose voiceprint this utterance may refine


def _key(room: str) -> str:
    return (room or "").strip().casefold()


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


class People:
    def __init__(self, names: dict[str, str]) -> None:
        self.names = dict(names)
        # room -> source -> person -> evidence
        self._seen: dict[str, dict[str, dict[str, Evidence]]] = {}
        # person -> (how it was heard, room) -> (signal strength, when): to place
        # a phone heard in several rooms where it is loudest
        self._signal: dict[str, dict[tuple[str, str], tuple[int, float]]] = {}
        # room -> the last request's speaker: (key or None, posterior, how, when)
        self._said: dict[str, tuple[str | None, float, str, float]] = {}
        # who is at home, and when the house sensor said so last
        self._home: set[str] = set()
        self._home_at: float | None = None

    def add(self, key: str, name: str) -> None:
        self.names[str(key)] = str(name)

    def remove(self, key: str) -> None:
        self.names.pop(str(key), None)

    def observe(self, room: str, source: str, person: str, confidence, now: float) -> None:
        if source not in SOURCES or person not in self.names or not room:
            return
        if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
            return
        self._seen.setdefault(_key(room), {}).setdefault(source, {})[person] = Evidence(
            person, float(confidence), now)

    def observe_phone(self, room: str, person: str, rssi, now: float, via: str = "ble") -> None:
        """A room sensor heard `person`'s phone at `rssi` dBm. Signal
        strengths are only compared between sensors of one kind (`via`):
        Bluetooth and Wi-Fi are on different scales."""
        if person not in self.names or not room or type(rssi) is not int:
            return
        self._signal.setdefault(person, {})[(str(via), _key(room))] = (rssi, now)

    def _placed(self, room: str, now: float) -> set[str]:
        """Whose phones are in `room`: heard there recently, and not much
        quieter there than in their loudest room."""
        here = _key(room)
        out = set()
        for person, heard in self._signal.items():
            for via in {v for v, _ in heard}:
                recent = {r: s for (v, r), (s, at) in heard.items()
                          if v == via and now - at <= WINDOW["phone"]}
                if here in recent and recent[here] >= max(recent.values()) - ROOM_MARGIN_DB:
                    out.add(person)
        return out

    def remember(self, room: str, a: "Assessment", now: float) -> None:
        """Who spoke last in `room` (for Home Assistant's "probably" sensor)."""
        if room:
            self._said[_key(room)] = (a.speaker, a.posterior, a.how, now)

    def last_speaker(self, room: str, now: float, within: float = 300.0) -> tuple[str | None, float, str]:
        """(key or None, posterior, how) of the last request in `room` in the
        last `within` seconds; (None, 0, "") otherwise."""
        hit = self._said.get(_key(room))
        if hit is None or now - hit[3] > within or (hit[0] is not None and hit[0] not in self.names):
            return None, 0.0, ""
        return hit[0], hit[1], hit[2]

    def observe_home(self, persons, now: float) -> None:
        """The full set of people whose phones are on the home network; it
        replaces the last one."""
        if not isinstance(persons, (list, tuple, set)):
            return
        self._home = {p for p in persons if isinstance(p, str) and p in self.names}
        self._home_at = now

    def home(self, now: float) -> set[str]:
        if self._home_at is None or now - self._home_at > HOME_WINDOW:
            return set()
        return set(self._home)

    def forget_faces(self, room: str) -> None:
        self._seen.get(_key(room), {}).pop("face", None)

    def _recent(self, room: str, source: str, now: float) -> dict[str, float]:
        found = self._seen.get(_key(room), {}).get(source, {})
        return {e.person: e.confidence for e in found.values()
                if now - e.at <= WINDOW[source] and e.confidence >= MIN_CONFIDENCE[source]}

    def assess(self, room: str, now: float, voice: dict[str, float] | None = None,
               last: str | None = None) -> Assessment:
        """Who is speaking in `room`, given this request's voice scores and the
        speaker earlier in the conversation."""
        phones = self._recent(room, "phone", now)
        phones.update({p: 1.0 for p in self._placed(room, now)})
        faces = self._recent(room, "face", now)
        aimed = self._recent(room, "aimed", now)
        home = self.home(now)
        cards = self._recent(room, "card", now)
        taps = self._recent(room, "tap", now)
        voice = {k: float(v) for k, v in (voice or {}).items()
                 if k in self.names and type(v) in (int, float)}

        weights: dict[str, float] = {}
        for person in self.names:
            w = PRIOR
            if person in phones:
                w *= PHONE
            if person in home:
                w *= HOME
            if person in cards or person in taps:
                w *= CARD
            if person in faces:
                w *= FACE
            if person == last:
                w *= CONTINUITY
            if voice:
                w *= math.exp(VOICE_SHARPNESS * voice.get(person, 0.0))
            if person in aimed:
                w *= 1 + AIMED * aimed[person]
            weights[person] = w
        unknown = PRIOR_UNKNOWN * (math.exp(VOICE_SHARPNESS * VOICE_UNKNOWN) if voice else 1.0)

        total = sum(weights.values()) + unknown
        ranked = sorted(weights, key=weights.get, reverse=True)
        a = Assessment()
        if ranked:
            top = ranked[0]
            runner_up = max([unknown] + [weights[p] for p in ranked[1:]])
            a.posterior = weights[top] / total
            if a.posterior >= POSTERIOR_MIN and weights[top] >= LEAD_MIN * runner_up:
                a.speaker = top
                by = []
                if voice and voice.get(top, 0.0) == max(voice.values()):
                    by.append("voice")
                if top in aimed:
                    by.append("face")
                if top in cards:
                    by.append("card")
                if top in taps:
                    by.append("phone tap")
                a.how = " and ".join(by) or ("presence" if top in phones or top in faces else "conversation")
                second = max([v for k, v in voice.items() if k != top], default=0.0)
                if aimed.get(top, 0.0) >= LEARN_AIMED or (
                        voice.get(top, 0.0) >= LEARN_VOICE and voice[top] - second >= LEARN_MARGIN):
                    a.learn = top
        a.nearby = [p for p in dict.fromkeys(list(faces) + list(aimed) + list(phones) + list(cards) + list(taps))
                    if p != a.speaker]
        return a

    def line(self, room: str, now: float, voice: dict[str, float] | None = None,
             last: str | None = None) -> str | None:
        """The hint for a request from `room`, or None when nothing is known."""
        a = self.assess(room, now, voice, last)
        if a.speaker is None and not a.nearby:
            return None
        parts = []
        if a.speaker:
            parts.append(f"You are probably talking to {self.names[a.speaker]} (recognised by {a.how}).")
        else:
            parts.append("You don't know who is speaking.")
        if a.nearby:
            names = sorted(self.names[p] for p in a.nearby)
            parts.append(f"{_names(names)} {'is' if len(names) == 1 else 'are'} nearby.")
        parts.append("Recognition is a guess: use names to be friendly, never to decide or allow anything.")
        return " ".join(parts)
