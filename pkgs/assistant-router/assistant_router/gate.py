"""T0: decisions that need no model.

Measured on real transcripts (2026-10-08): the microphone picks up the
assistant's own replies, radio and film dialogue, and one-word fragments, and
a small model acts on them. Rules catch those for free, and send requests that
plainly need knowledge or search straight to the cloud tier.
"""
from __future__ import annotations

import difflib
import re
from typing import Literal

Verdict = Literal["reject", "escalate", "stop", "pass"]

_WORD = re.compile(r"[a-z0-9']+")
FILLERS = {"okay", "ok", "yes", "no", "please", "thanks", "thank", "you", "hello", "hi", "bye", "what", "done",
           "blame", "hmm", "um", "uh", "right", "level", "sure", "yeah", "yep", "nope", "nah"}
ACTION_ONLY = {"turn", "play", "stop", "on", "off", "switch", "set", "start"}
STOP_PHRASES = {"stop", "please stop", "stop stop", "cancel", "never mind", "nevermind", "be quiet", "shut up"}
ALL_WORDS = re.compile(r"\b(all|every|everything|everywhere|whole house|all of the)\b")
# Several devices: a plural or two targets joined by "and". The local model
# would pick one of them.
SEVERAL = re.compile(r"\b(lights|lamps|switches|fans|blinds|curtains|shutters|speakers|tvs|plugs)\b|\b and (the )?[a-z]")
CLOUD = re.compile(
    r"^(why|how|what is|what's the|what are|who|when|where|explain|tell me|can you tell|read|remind|"
    r"set (a |the )?remind|wake me|what happened)\b|\b(news|weather tomorrow|this weekend|recipe|podcast|"
    r"by [a-z]+|something (like|relaxing|upbeat|calm)|radio [a-z]+|[a-z]+ radio)\b")
MAX_COMMAND_WORDS = 18
# The middle of someone else's sentence (radio, TV, a conversation): nobody
# starts a request with these words.
CONTINUATION = {"to", "that", "and", "but", "so", "except", "because", "which", "than", "then", "of", "as",
                "or", "nor", "whenever", "while", "whereas", "although"}
# How the assistant talks, heard back from a satellite whose reply this one
# never made (the kitchen hears the bedroom).
ASSISTANT_PHRASES = re.compile(r"\blet me know if\b|\bit's (on|off)\b|\bdone[.!]?$")
# A request someone makes starts with one of these; the assistant's replies
# ("Turned off...", "Okay, it's on") don't, so a request is never an echo.
COMMAND_START = {"turn", "switch", "play", "pause", "stop", "set", "open", "close", "put", "start", "skip",
                 "increase", "decrease", "lower", "raise", "volume", "next", "resume", "dim", "make"}
# Conjunctions that can open a request ("So, find the matrix", "And turn off the
# light") even though they usually mark the middle of someone else's sentence.
# They are dropped when a request follows, and left in place (so the
# continuation rule rejects them) when none does.
_LEAD_IN = re.compile(r"^\s*(?:(?:and|but|so|or|then)\b[\s,]*)+", re.IGNORECASE)
# A request can follow a lead-in as a device command (COMMAND_START), a media
# search (these verbs), or a question the cloud answers (CLOUD).
SEARCH_START = {"find", "search", "look", "show"}


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _after_lead_in(text: str) -> str:
    """The text without a leading conjunction ("So, find ..." -> "find ...")."""
    return _LEAD_IN.sub("", text)


def _is_request(text: str) -> bool:
    """Whether these words open a request: a command, a search or a question."""
    words = _words(text)
    return bool(words) and (words[0] in COMMAND_START or words[0] in SEARCH_START
                            or CLOUD.search(text) is not None)


def _is_echo(text: str, last_reply: str) -> bool:
    if not last_reply:
        return False
    words = _words(text)
    a, b = " ".join(words), " ".join(_words(last_reply))
    if not a or words[0] in COMMAND_START:
        return False
    return a in b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def gate(text: str, last_reply: str) -> Verdict:
    # A leading conjunction can open a request ("So, find the matrix") or be
    # the middle of someone else's sentence ("and then the lights went out").
    # Strip it when a request follows, so the rules below judge that request;
    # otherwise it stays, and the continuation rule rejects it.
    stripped = _after_lead_in(text)
    if stripped != text and _is_request(stripped):
        text = stripped
    words = _words(text)
    joined = " ".join(words)
    # "Yes" to the assistant's own question is an answer, not a fragment: to
    # the cloud model, which has the conversation.
    if words and all(w in FILLERS for w in words) and last_reply.rstrip().endswith("?"):
        return "escalate"
    if not words or all(w in FILLERS for w in words):
        return "reject"
    if _is_echo(text, last_reply):
        return "reject"
    if joined in STOP_PHRASES or set(words) <= {"stop", "please"}:
        return "stop"
    # A bare verb, or a verb and a goodbye: nothing to act on.
    if all(w in ACTION_ONLY | FILLERS for w in words):
        return "reject"
    if words[0] in CONTINUATION or ASSISTANT_PHRASES.search(text.lower().strip()):
        return "reject"
    # Several sentences of speech: the radio or a film, not a command.
    sentences = text.count(".") + text.count("?") + text.count("!")
    if len(words) > MAX_COMMAND_WORDS or (sentences >= 2 and len(words) >= 12) or (sentences >= 3 and len(words) > 10):
        return "reject"
    if ALL_WORDS.search(joined) or SEVERAL.search(joined) or CLOUD.search(joined):
        return "escalate"
    return "pass"
