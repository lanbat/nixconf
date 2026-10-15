#!/usr/bin/env python3
"""The satellite's microphone, brought back when its capture breaks
(modules/core/voice-satellite-audio.nix).

A USB microphone that follows another card's clock (the PlayStation Eye, at
16 kHz, behind a 48 kHz USB speaker) can fall into a loop where every read of
it fails: "<pcm>:<card>c: snd_pcm_mmap_commit error ...: Broken pipe" in
PipeWire's log, 25 times a second, for hours. Its audio is then torn every
40 ms, so the wake word is never heard and a direction read from it is noise.

This follows PipeWire's log (journalctl). ERRORS such failures of the
microphone's card within WINDOW_S seconds reset it: its card profile off and
on again, which reopens it cleanly; then Linux Voice Assistant restarts, since
the profile change moves its recording off the echo-cancelled microphone (it
opens that one again at start). At most once every COOLDOWN_S.

The microphone is found by its USB ID (MIC_USB_ID, vvvv:pppp) in pw-dump, each
time: its card number changes when it is plugged in again.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import deque


def log(msg: str) -> None:
    print(f"mic-watchdog: {msg}", flush=True)


def find_mic(dump: list, usb_id: str) -> tuple[int, str, int] | None:
    """(PipeWire device id, ALSA card number, active profile index) of the
    sound card with this USB ID in pw-dump's objects, or None."""
    vendor, _, product = usb_id.partition(":")
    for obj in dump if isinstance(dump, list) else []:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Device":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if props.get("device.api") != "alsa" or props.get("api.alsa.card") is None:
            continue  # the camera's video device has the same IDs
        if props.get("device.vendor.id", "").lower() != f"0x{vendor.lower()}" \
                or props.get("device.product.id", "").lower() != f"0x{product.lower()}":
            continue
        active = (info.get("params") or {}).get("Profile") or []
        index = active[0].get("index") if active and isinstance(active[0], dict) else None
        if type(obj.get("id")) is int and type(index) is int:
            return obj["id"], str(props["api.alsa.card"]), index
    return None


class Storm:
    """Counts the microphone card's failed capture reads; says when to reset."""

    def __init__(self, errors: int, window_s: float, cooldown_s: float) -> None:
        self.errors, self.window_s, self.cooldown_s = errors, window_s, cooldown_s
        self.card: str | None = None
        self._pattern: re.Pattern | None = None
        self._times: deque[float] = deque()
        self._reset_at: float | None = None

    def watch(self, card: str | None) -> None:
        if card != self.card:
            self.card, self._times = card, deque()
            self._pattern = None if card is None else re.compile(
                rf":{re.escape(card)}c: snd_pcm_mmap_commit error .*Broken pipe")

    def add(self, line: str, now: float) -> bool:
        """A PipeWire log line; True when the microphone should be reset now."""
        if self._pattern is None or not self._pattern.search(line):
            return False
        self._times.append(now)
        while self._times and now - self._times[0] > self.window_s:
            self._times.popleft()
        if len(self._times) < self.errors:
            return False
        if self._reset_at is not None and now - self._reset_at < self.cooldown_s:
            return False
        self._reset_at = now
        self._times.clear()
        return True


def run(cmd: list[str], timeout: float = 10.0) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False).stdout


def locate(pw_dump: str, usb_id: str) -> tuple[int, str, int] | None:
    try:
        return find_mic(json.loads(run([pw_dump]) or "[]"), usb_id)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None


def reset(env: dict, mic: tuple[int, str, int]) -> None:
    device, card, profile = mic
    log(f"card {card}: its capture keeps failing; reopening it (profile {profile}) and restarting the voice assistant")
    run([env["WPCTL"], "set-profile", str(device), "0"])
    time.sleep(1.5)
    run([env["WPCTL"], "set-profile", str(device), str(profile)])
    time.sleep(2.0)
    run([env["SYSTEMCTL"], "restart", env.get("LVA_UNIT", "linux-voice-assistant.service")], timeout=60)


def main() -> None:
    env = os.environ
    usb_id = env["MIC_USB_ID"]
    storm = Storm(int(env.get("WATCH_ERRORS", "30")), float(env.get("WATCH_WINDOW_S", "60")),
                  float(env.get("WATCH_COOLDOWN_S", "300")))
    mic = locate(env["PW_DUMP"], usb_id)
    storm.watch(mic[1] if mic else None)
    log(f"watching card {storm.card} ({usb_id})" if mic else f"no card with USB ID {usb_id} yet")
    proc = subprocess.Popen([env["JOURNALCTL"], "-f", "-n", "0", "-o", "cat", "SYSLOG_IDENTIFIER=pipewire"],
                            stdout=subprocess.PIPE, text=True)
    looked = time.monotonic()
    assert proc.stdout is not None
    for line in proc.stdout:
        now = time.monotonic()
        if now - looked > 60:  # plugged in again: a new card number
            looked, mic = now, locate(env["PW_DUMP"], usb_id)
            storm.watch(mic[1] if mic else None)
        if storm.add(line, now):
            mic = locate(env["PW_DUMP"], usb_id)
            if mic is not None:
                reset(env, mic)
                storm.watch(mic[1])
    sys.exit(proc.wait())


if __name__ == "__main__":
    main()
