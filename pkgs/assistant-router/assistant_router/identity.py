"""Meeting Nabu: enrolment by just talking (docs/people.md, "Meeting Nabu").

Nobody needs a phone, an app or an account to be known. After answering
someone whose voice it doesn't know, in a room with a robot, Nabu asks once
(a room at most once a day) "What's your name?". A name is consent: from that
conversation it learns the voice, the robot's camera adds the face, and a card
held on the robot's head is theirs. A decline keeps nothing. "Remember me"
does the same on request; a known person gets a top-up.

This module only reads what was said: the commands and the answer to the
name question. The router (server.py) does the binding.
"""
from __future__ import annotations

import re

OFFER = "By the way, I don't think we've met. What's your name?"
ASK_NAME = "Sure! What's your name?"
DECLINED = "No problem!"
NOT_SURE = "I'm not sure who you are yet. Tell me your name, and I'll remember you."
CARD_HINT = "If you have a card, hold it on my head now."
MORE_VOICE = "Tell me a little about your day, so I learn your voice?"

_SAID = re.compile(r"[^a-z' -]+")


def _said(text: str) -> str:
    return re.sub(r"\s+", " ", _SAID.sub(" ", (text or "").lower())).strip(" '-")


# "Remember me", "learn me", "remember who I am", "get to know me".
_REMEMBER = re.compile(r"^(?:(?:hey|okay|ok|please|nabu|can you|could you|would you)\s+)*"
                       r"(?:remember|learn|save|register|get to know)\s+(?:me|who i am|my (?:voice|face))"
                       r"(?:\s+please)?$")
# "Forget my card", "remove my cards" (and the card-only forms of before).
_FORGET_CARD = re.compile(r"\b(forget|remove|delete)\b.*\b(this|my|the) (card|cards|tag|tags)\b")
# "Call me Kiki."
_CALL_ME = re.compile(r"^(?:(?:please|nabu|okay|ok)\s+)*(?:call me|my name is|i'm called|i am called)\s+"
                      r"(?P<name>[a-z][a-z' -]{0,38})$")


def command(text: str) -> tuple[str, str | None] | None:
    """("remember", None), ("forget_card", None), ("call_me", name), or None."""
    said = _said(text)
    if _FORGET_CARD.search(said):
        return "forget_card", None
    if _REMEMBER.match(said):
        return "remember", None
    m = _CALL_ME.match(said)
    if m:
        return "call_me", _title(m.group("name"))
    return None


_DECLINE = re.compile(r"^(?:(?:oh|um|uh|well)\s+)*(?:no|nope|nah|no thanks|no thank you|not now|maybe later|later|"
                      r"skip|skip it|pass|never mind|nevermind|i'd rather not|rather not|don't|do not|"
                      r"i'm (?:just )?(?:visiting|a guest)|just visiting|i am (?:just )?(?:visiting|a guest))"
                      r"(?:\s+(?:thanks|thank you|please))?$")
# "I'm Jane", "my name is Jane", "it's Jane", "this is Jane", "call me Jane".
_NAME_INTRO = re.compile(r"^(?:(?:hi|hello|hey|oh|um|uh|well|okay|ok|sure|yes|yeah)\s+)*"
                         r"(?:i'm|i am|my name is|my name's|it's|it is|this is|call me|they call me|"
                         r"you can call me|i go by)\s+(?P<name>.+)$")
# Words that make a bare reply something other than a name.
_NOT_NAMES = frozenset("""
the a an and or what who why when where how which turn switch set play stop pause cancel volume
lights light music timer alarm weather time yes yeah sure okay ok hello hi hey thanks thank you
please nothing never mind sorry
tired fine good great busy back home here ready done sleepy hungry cold hot ill sick bored lost
""".split())


def _title(name: str) -> str:
    """"mary-ann o'neil" -> "Mary-Ann O'neil": each word and hyphenated part capitalised."""
    return " ".join("-".join(p.capitalize() for p in w.split("-")) for w in name.split())[:40]


# A decline anywhere in the answer: "No thanks, I'm just visiting".
_DECLINE_ANY = re.compile(r"\b(?:no thanks|no thank you|not now|maybe later|just visiting|i'm a guest|"
                          r"i am a guest|rather not|never mind|nevermind|don't remember me|do not remember me)\b")


def answer(text: str) -> tuple[str, str | None]:
    """An answer to "What's your name?": ("name", "Jane"), ("decline", None),
    or ("unclear", None) when it's neither (the conversation goes on)."""
    said = _said(text)
    if not said:
        return "unclear", None
    if _DECLINE.match(said) or _DECLINE_ANY.search(said) or said.split()[0] in ("no", "nope", "nah"):
        return "decline", None
    m = _NAME_INTRO.match(said)
    words = (m.group("name") if m else said).split()
    # A name is one to three words, none of them a command or filler word.
    if 1 <= len(words) <= 3 and all(w.isalpha() or "'" in w or "-" in w for w in words) \
            and not any(w in _NOT_NAMES for w in words):
        return "name", _title(" ".join(words))
    return "unclear", None
