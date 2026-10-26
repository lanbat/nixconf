"""Who the assistant is talking to: a hint from voice, faces and phones.

Evidence arrives as (room, source, person key, confidence). The person keys
are the profile's people (lanbat.deployment.people, and those enrolled since);
anything else is dropped, so no free text from a satellite or a robot reaches
a prompt. The sources:

- "phone": a person's phone heard in the room (pkgs/room-presence), last 25 s;
  heard by sensors in several rooms (through walls), it counts only where it
  is loudest, or in each room within ROOM_MARGIN_DB of that;
- "face": a face recognised in the room (cameras via Frigate), last 10 s;
- "aimed": the face the room's robot saw after turning to the voice, last 10 s;
- "seen": a face that came into the room's robot's view (it shows the router
  one on arrival and every minute while someone stays), last 90 s: they are
  here, not necessarily the speaker;
- "card": a person's NFC card tapped on the room's robot (cards.py),
  last 2 min: strong, it names them unless the voice clearly disagrees;
- "tap": a person's phone held to the room's robot, and their login on its
  page (tap.py), last 2 min: as strong as a card;
- "voice": an enrolled voice clearly matched in the room (note_voice), last
  5 min: they are there, whoever speaks next;
- home: a person's phone on the home network (pkgs/wifi-presence), for the
  whole house, last 3 min;
- and, per request, the voice: a score for every enrolled person (voice-id).

presence() puts the same evidence together per person, for "who is in this
room" (Home Assistant) rather than "who is speaking", and where() picks the
room a person is most likely in.

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
from typing import Callable

SOURCES = ("face", "aimed", "seen", "phone", "card", "tap", "voice")
WINDOW = {"face": 10.0, "aimed": 10.0, "seen": 90.0, "phone": 25.0, "card": 120.0, "tap": 120.0,
          "voice": 300.0}
MIN_CONFIDENCE = {"face": 0.6, "aimed": 0.6, "seen": 0.6, "phone": 0.5, "card": 0.5, "tap": 0.5,
                  "voice": 0.7}

# The weights. Tuned on the scenarios in tests/test_people.py.
PRIOR = 0.05            # every enrolled person
PRIOR_UNKNOWN = 0.1     # someone not enrolled
PHONE = 4.0             # × prior: their phone is in the room
HOME = 2.0              # × prior: their phone is on the home network
CARD = 100.0            # × prior: they just tapped their card, or their phone, on the robot
HOME_WINDOW = 180.0     # the house sensor repeats its set every minute
ROOM_MARGIN_DB = 6      # a phone this much quieter than in its loudest room isn't there
SWITCH_DB = 4           # ...and another room must be this much louder to take them over
SWITCH_HOLD_S = 6.0     # ...for this long: one noisy sample must not move the music
FACE = 6.0              # × prior: their face is in the room
VOICE_PRESENT = FACE    # × prior: their voice was clearly heard in the room (last 5 min)
CONTINUITY = 5.0        # × prior: they were the speaker earlier in this conversation
VOICE_SHARPNESS = 10.0  # likelihood exp(k·cosine)
VOICE_UNKNOWN = 0.25    # the cosine an unenrolled voice scores against itself
AIMED = 1200.0          # × (1 + AIMED·confidence): the face the robot turned to
VOICE_EVIDENCE_S = 300.0  # the last voice match is shown this long (evidence())
POSTERIOR_MIN = 0.8
LEAD_MIN = 3.0          # × the runner-up (the unknown candidate included)
LEARN_VOICE = 0.8       # a voice this close, and LEARN_MARGIN ahead, teaches itself
LEARN_MARGIN = 0.2
LEARN_AIMED = 0.8       # an aimed face this sure teaches the voice
# presence(): how likely each source says someone is in the room, combined as
# independent chances (noisy-OR). A face, the robot's look and a voice count
# as their own confidence.
PRESENT_CARD = 0.98     # a tap of their card or phone on the room's robot
PRESENT_PHONE = 0.8     # their phone placed in the room
PRESENT_HOME = 0.2      # their phone on the home network: somewhere in the house
HERE_MIN = 0.5          # this sure, they count as in the room


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
        # People nobody has met (unknowns.py): in `names`, never greeted or counted as present.
        self.strangers: set[str] = set()
        # Told who was identified and when (evidence.py learns which devices arrive with whom).
        self.on_identified: Callable[[str, float], None] | None = None
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
        # room -> the last sentence's voice scores, and when (evidence())
        self._voice: dict[str, tuple[dict[str, float], float]] = {}
        # room key -> the room's name as it was last given (where())
        self._rooms: dict[str, str] = {}
        # person -> the room where() last put them (a key), and the room trying
        # to take them with when it started leading: the hysteresis that keeps
        # two rooms hearing one phone through a wall from taking turns.
        self._held: dict[str, str] = {}
        self._challenger: dict[str, tuple[str, float]] = {}

    def add(self, key: str, name: str) -> None:
        self.names[str(key)] = str(name)

    def remove(self, key: str) -> None:
        self.names.pop(str(key), None)
        self.strangers.discard(str(key))
        self._held.pop(str(key), None)
        self._challenger.pop(str(key), None)

    def observe(self, room: str, source: str, person: str, confidence, now: float) -> None:
        if source not in SOURCES or person not in self.names or not room:
            return
        if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
            return
        if self.on_identified is not None:
            self.on_identified(person, now)
        if person in self.strangers:
            return
        self._rooms[_key(room)] = room.strip()
        self._seen.setdefault(_key(room), {}).setdefault(source, {})[person] = Evidence(
            person, float(confidence), now)

    def observe_phone(self, room: str, person: str, rssi, now: float, via: str = "ble") -> None:
        """A room sensor heard `person`'s phone at `rssi` dBm. Signal
        strengths are only compared between sensors of one kind (`via`):
        Bluetooth and Wi-Fi are on different scales."""
        if person not in self.names or not room or type(rssi) is not int:
            return
        if self.on_identified is not None:
            self.on_identified(person, now)
        self._rooms[_key(room)] = room.strip()
        self._signal.setdefault(person, {})[(str(via), _key(room))] = (rssi, now)

    def _placed(self, room: str, now: float, only: str | None = None) -> set[str]:
        """Whose phones are in `room`: heard there recently, and not much
        quieter there than in their loudest room (by `only` one kind of
        sensor, "ble" or "wifi", when given)."""
        here = _key(room)
        out = set()
        for person, heard in self._signal.items():
            for via in {v for v, _ in heard if only is None or v == only}:
                recent = {r: s for (v, r), (s, at) in heard.items()
                          if v == via and now - at <= WINDOW["phone"]}
                if here in recent and recent[here] >= max(recent.values()) - ROOM_MARGIN_DB:
                    out.add(person)
        return out

    def note_voice(self, room: str, voice: dict[str, float] | None, now: float) -> None:
        """The last sentence's voice scores in `room`, for evidence(); and a
        voice that clearly matches one enrolled person puts them in the room
        ("voice", 5 min), as a face would."""
        if room and voice:
            scores = {k: float(v) for k, v in voice.items() if k in self.names and type(v) in (int, float)}
            if scores:
                self._voice[_key(room)] = (scores, now)
                best = max(scores, key=scores.get)
                second = max([v for k, v in scores.items() if k != best], default=0.0)
                if scores[best] >= MIN_CONFIDENCE["voice"] and scores[best] - second >= LEARN_MARGIN:
                    self.observe(room, "voice", best, min(scores[best], 1.0), now)

    def evidence(self, room: str, now: float) -> dict[str, dict[str, float]]:
        """What each source says about `room` now: person key -> confidence,
        per source. For Home Assistant's view of the recognition; the same
        evidence assess() weighs, with nothing added."""
        seen, aimed = self._recent(room, "seen", now), self._recent(room, "aimed", now)
        out = {
            "camera": self._recent(room, "face", now),
            "robot": {p: max(seen.get(p, 0.0), aimed.get(p, 0.0)) for p in {**seen, **aimed}},
            "bluetooth": {p: 1.0 for p in self._placed(room, now, only="ble")},
            "wifi": {p: 1.0 for p in self._placed(room, now, only="wifi")},
            "card": {**self._recent(room, "card", now), **self._recent(room, "tap", now)},
            "home": {p: 1.0 for p in self.home(now)},
        }
        out["bluetooth"].update(self._recent(room, "phone", now))
        voice = self._voice.get(_key(room))
        out["voice"] = {}
        if voice is not None and now - voice[1] <= VOICE_EVIDENCE_S:
            best = max(voice[0], key=voice[0].get)
            if voice[0][best] >= VOICE_UNKNOWN:
                out["voice"] = {best: round(voice[0][best], 2)}
        return out

    def presence(self, room: str, now: float) -> dict[str, tuple[float, list[str]]]:
        """Who is in `room` now: person key -> (how sure, 0..1; the sources
        that say so, as evidence() names them). Each source is a chance they
        are here (a tap or card 0.98, a face, the robot's look or the voice
        its confidence, their phone placed here 0.8, their phone at home 0.2),
        combined as independent (noisy-OR)."""
        found: dict[str, dict[str, float]] = {}

        def add(source: str, person: str, chance: float) -> None:
            if person in self.names:
                by = found.setdefault(person, {})
                by[source] = max(chance, by.get(source, 0.0))

        for source, name in (("face", "camera"), ("aimed", "robot"), ("seen", "robot"), ("voice", "voice")):
            for person, confidence in self._recent(room, source, now).items():
                add(name, person, confidence)
        for source in ("card", "tap"):
            for person in self._recent(room, source, now):
                add("card", person, PRESENT_CARD)
        for person in self._placed(room, now, only="ble") | set(self._recent(room, "phone", now)):
            add("bluetooth", person, PRESENT_PHONE)
        for person in self._placed(room, now, only="wifi"):
            add("wifi", person, PRESENT_PHONE)
        for person in self.home(now):
            add("home", person, PRESENT_HOME)
        out = {}
        for person, by in found.items():
            missed = 1.0
            for chance in by.values():
                missed *= 1.0 - chance
            out[person] = (round(1.0 - missed, 3), sorted(by))
        return out

    def here(self, room: str, now: float) -> dict[str, tuple[float, list[str]]]:
        """presence() of those that count as in `room`: sure enough, and by
        more than their phone being somewhere in the house."""
        return {p: (sure, by) for p, (sure, by) in self.presence(room, now).items()
                if sure >= HERE_MIN and set(by) - {"home"}}

    def where(self, person: str, now: float) -> tuple[str, float, list[str]]:
        """Where `person` most likely is: (the room, how sure, the sources);
        ("home", 0.2, ["home"]) when only their phone on the home network says
        so, and ("away", 0, []) when nothing does."""
        rooms = {r for r, by in self._seen.items() if any(person in found for found in by.values())}
        rooms |= {r for (_, r) in self._signal.get(person, {})}
        best: tuple[str, float, list[str]] = ("", 0.0, [])
        best_rssi: int | None = None
        for room in sorted(rooms):
            sure, by = self.here(room, now).get(person, (0.0, []))
            if sure <= 0.0:
                continue
            # Two rooms hear a phone through the wall and tie on confidence; the
            # loudest one wins, not the first alphabetically (someone in the
            # Office must not read as "Bedroom 1" just because it sorts first).
            rssi = self._loudest_ble(person, room, now)
            if sure > best[1] or (
                    sure == best[1]
                    and rssi is not None
                    and (best_rssi is None or rssi > best_rssi)):
                best = (self._rooms.get(room, room), sure, by)
                best_rssi = rssi
        if best[0]:
            best = self._steady(person, best, best_rssi, now)
            self._held[person] = _key(best[0])
            return best
        self._challenger.pop(person, None)
        if person in self.home(now):
            return "home", PRESENT_HOME, ["home"]
        return "away", 0.0, []

    def _steady(self, person: str, best: tuple[str, float, list[str]],
                best_rssi: int | None, now: float) -> tuple[str, float, list[str]]:
        """Keep someone in the room they are in until another is clearly louder
        for a while. Two rooms hear one phone through a wall at nearly the same
        strength; the loudest sample alone flips with the radio's noise, and the
        music would follow each flip. A face, a voice or a card in the other room
        is not noise: it moves them at once, as does the old room going quiet."""
        held = self._held.get(person)
        if held is None or held == _key(best[0]):
            self._challenger.pop(person, None)
            return best
        if set(best[2]) - {"bluetooth", "wifi", "home"}:
            self._challenger.pop(person, None)
            return best
        held_sure, held_by = self.here(held, now).get(person, (0.0, []))
        if held_sure <= 0.0:
            self._challenger.pop(person, None)
            return best
        held_rssi = self._loudest_ble(person, held, now)
        if best_rssi is None or held_rssi is None or best_rssi - held_rssi < SWITCH_DB:
            self._challenger.pop(person, None)
            return self._held_answer(held, held_sure, held_by)
        started = self._challenger.get(person)
        if started is None or started[0] != _key(best[0]):
            self._challenger[person] = (_key(best[0]), now)
            return self._held_answer(held, held_sure, held_by)
        if now - started[1] < SWITCH_HOLD_S:
            return self._held_answer(held, held_sure, held_by)
        self._challenger.pop(person, None)
        return best

    def _held_answer(self, room: str, sure: float, by: list[str]) -> tuple[str, float, list[str]]:
        return self._rooms.get(room, room), sure, by

    def _loudest_ble(self, person: str, room: str, now: float) -> int | None:
        """The loudest recent Bluetooth RSSI for `person` in `room`, or None:
        the tie-break between rooms that hear the same phone."""
        here = _key(room)
        best: int | None = None
        for (via, r), (rssi, at) in self._signal.get(person, {}).items():
            if via == "ble" and r == here and now - at <= WINDOW["phone"]:
                if best is None or rssi > best:
                    best = rssi
        return best

    def bound(self, room: str, now: float) -> tuple[str | None, float]:
        """Who last tapped their phone or card on `room`'s robot, in the last
        two minutes, and when; (None, 0) when nobody did. What the room notices
        meanwhile (a face, a voice) may be learned as theirs."""
        found = [e for source in ("tap", "card")
                 for e in self._seen.get(_key(room), {}).get(source, {}).values()
                 if now - e.at <= WINDOW[source] and e.person in self.names]
        if not found:
            return None, 0.0
        latest = max(found, key=lambda e: e.at)
        return latest.person, latest.at

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
        seen = self._recent(room, "seen", now)
        home = self.home(now)
        cards = self._recent(room, "card", now)
        taps = self._recent(room, "tap", now)
        heard = self._recent(room, "voice", now)
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
            if person in seen:
                w *= FACE
            if person in heard:
                w *= VOICE_PRESENT
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
                a.how = " and ".join(by) or (
                    "presence" if top in phones or top in faces or top in seen or top in heard else "conversation")
                second = max([v for k, v in voice.items() if k != top], default=0.0)
                # A tap or a card names them for sure: what they say teaches the
                # voice. Unless the voice is clearly someone else's, already
                # enrolled: then someone else is talking in their window.
                someone_else = second >= LEARN_VOICE and second - voice.get(top, 0.0) >= LEARN_MARGIN
                if not someone_else and (aimed.get(top, 0.0) >= LEARN_AIMED or top in cards or top in taps or (
                        voice.get(top, 0.0) >= LEARN_VOICE and voice[top] - second >= LEARN_MARGIN)):
                    a.learn = top
        a.nearby = [p for p in self.here(room, now) if p != a.speaker]
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
