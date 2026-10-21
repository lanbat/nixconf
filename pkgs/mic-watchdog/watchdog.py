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

A second way for it to break leaves no trace in the log: the card stays open
and PipeWire hands out digital silence, because the device has stopped sending
(2026-10-21: ALSA's hw_ptr stuck at 0, a direct read failing with EIO, and
"current rate N is different from the runtime rate 16000" with N garbage in
the kernel log). So every SILENCE_INTERVAL_S seconds this also reads two
seconds from the microphone's capture node (MIC_NODE); a window of nothing but
zero samples, which a live microphone never gives, is a silent check, and
SILENCE_CHECKS of them in a row climb a ladder, one step per run of silent
checks:

  1. reopen the card, as above;
  2. make the kernel enumerate the USB device again (the sysfs `authorized`
     switch, a software unplug and replug), then reopen the card;
  3. log, every NOTICE_S seconds, that only unplugging it will help.

Any sound at all puts it back at the bottom of the ladder. The 2026-10-21 stall
survived steps 1 and 2 and needed the plug pulled: step 3 is the honest end of
what software can do.
"""
from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path


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

    def cooled(self, now: float) -> None:
        """Something else reset the microphone just now: count that for the cooldown."""
        self._reset_at = now
        self._times.clear()

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


SAMPLE_RATE = 16000


class Silence:
    """Counts silent checks of the microphone; says which step of the ladder to
    take (see the top)."""

    def __init__(self, checks: int, notice_s: float) -> None:
        self.checks, self.notice_s = checks, notice_s
        self.run = 0
        self.step = 0
        self._noticed: float | None = None

    def add(self, silent: bool, now: float) -> str | None:
        """One check's result; "reset", "reauthorize", "replug" or None."""
        if not silent:
            self.run = self.step = 0
            self._noticed = None
            return None
        self.run += 1
        if self.run < self.checks:
            return None
        self.run = 0  # the next step comes after another run of silent checks
        self.step += 1
        if self.step == 1:
            return "reset"
        if self.step == 2:
            return "reauthorize"
        if self._noticed is None or now - self._noticed >= self.notice_s:
            self._noticed = now
            return "replug"
        return None

    def reopened(self) -> None:
        """The card was reopened (by this or the storm check): count afresh."""
        self.run = 0


def is_silent(data: bytes, wanted: int) -> bool:
    """True when a sample is nothing but zeros, or too short to be one: the
    capture node gave less than it should in the time allowed."""
    return len(data) < wanted or not any(data)


def sample(env: dict, node: str, seconds: float = 2.0) -> bytes:
    """About `seconds` of mono 16 kHz audio from a PipeWire node, as raw bytes
    (fewer if it does not come in time)."""
    wanted = int(SAMPLE_RATE * 2 * seconds)
    proc = subprocess.Popen(
        [env["PW_RECORD"], "--target", node, "--rate", str(SAMPLE_RATE), "--channels", "1", "--format", "s16", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=dict(env))
    data = b""
    try:
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        deadline = time.monotonic() + seconds + 4.0
        while len(data) < wanted and time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
    finally:
        proc.kill()
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
    return data[:wanted]


def usb_device(sysfs: str, usb_id: str) -> Path | None:
    """The sysfs directory of the USB device with this ID (vvvv:pppp)."""
    vendor, _, product = usb_id.lower().partition(":")
    for d in sorted(Path(sysfs).glob("*")):
        try:
            if (d / "idVendor").read_text().strip().lower() == vendor \
                    and (d / "idProduct").read_text().strip().lower() == product:
                return d
        except OSError:
            continue
    return None


def run(cmd: list[str], timeout: float = 10.0) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False).stdout


def locate(pw_dump: str, usb_id: str) -> tuple[int, str, int] | None:
    try:
        return find_mic(json.loads(run([pw_dump]) or "[]"), usb_id)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None


def reopen(env: dict, mic: tuple[int, str, int]) -> None:
    """The card's profile off and on, then Linux Voice Assistant restarted."""
    device, _, profile = mic
    run([env["WPCTL"], "set-profile", str(device), "0"])
    time.sleep(1.5)
    run([env["WPCTL"], "set-profile", str(device), str(profile)])
    time.sleep(2.0)
    run([env["SYSTEMCTL"], "restart", env.get("LVA_UNIT", "linux-voice-assistant.service")], timeout=60)


def reset(env: dict, mic: tuple[int, str, int]) -> None:
    log(f"card {mic[1]}: its capture keeps failing; reopening it (profile {mic[2]}) and restarting the voice assistant")
    reopen(env, mic)


def reauthorize(env: dict, usb_id: str) -> bool:
    """The USB device unplugged and plugged in again, in software."""
    device = usb_device(env.get("USB_SYSFS", "/sys/bus/usb/devices"), usb_id)
    if device is None:
        return False
    try:
        (device / "authorized").write_text("0")
        time.sleep(3.0)
        (device / "authorized").write_text("1")
    except OSError as e:
        log(f"{device.name}: could not switch it off and on: {e}")
        return False
    time.sleep(8.0)  # it enumerates again, and PipeWire opens its card
    return True


def watch_silence(env: dict, usb_id: str, silence: Silence, lock: threading.Lock, storm: Storm) -> None:
    """The silent-capture check, forever (its own thread)."""
    node = env["MIC_NODE"]
    interval = float(env.get("SILENCE_INTERVAL_S", "30"))
    while True:
        time.sleep(interval)
        mic = locate(env["PW_DUMP"], usb_id)
        if mic is None:  # unplugged, or PipeWire not up: nothing to measure
            silence.add(False, time.monotonic())
            continue
        try:
            silent = is_silent(sample(env, node), int(SAMPLE_RATE * 2 * 2.0) * 9 // 10)
        except OSError:
            continue
        with lock:
            action = silence.add(silent, time.monotonic())
            if action is None:
                continue
            if action == "replug":
                log(f"card {mic[1]}: still only silence after reopening it and enumerating it again; "
                    "unplug it and plug it in again (or cycle its hub's power)")
                continue
            if action == "reauthorize":
                log(f"card {mic[1]}: still only silence; enumerating the USB device again")
                if reauthorize(env, usb_id):
                    mic = locate(env["PW_DUMP"], usb_id) or mic
            else:
                log(f"card {mic[1]}: only silence from it; reopening it (profile {mic[2]}) and restarting the voice assistant")
            reopen(env, mic)
            silence.reopened()
            storm.watch(mic[1])
            storm.cooled(time.monotonic())


def main() -> None:
    env = os.environ
    usb_id = env["MIC_USB_ID"]
    storm = Storm(int(env.get("WATCH_ERRORS", "30")), float(env.get("WATCH_WINDOW_S", "60")),
                  float(env.get("WATCH_COOLDOWN_S", "300")))
    silence = Silence(int(env.get("SILENCE_CHECKS", "3")), float(env.get("NOTICE_S", "1800")))
    lock = threading.Lock()  # one reset at a time, from either check
    mic = locate(env["PW_DUMP"], usb_id)
    storm.watch(mic[1] if mic else None)
    log(f"watching card {storm.card} ({usb_id})" if mic else f"no card with USB ID {usb_id} yet")
    if env.get("MIC_NODE") and env.get("PW_RECORD"):
        threading.Thread(target=watch_silence, args=(env, usb_id, silence, lock, storm), daemon=True).start()
    proc = subprocess.Popen([env["JOURNALCTL"], "-f", "-n", "0", "-o", "cat", "SYSLOG_IDENTIFIER=pipewire"],
                            stdout=subprocess.PIPE, text=True)
    looked = time.monotonic()
    assert proc.stdout is not None
    for line in proc.stdout:
        now = time.monotonic()
        if now - looked > 60:  # plugged in again: a new card number
            looked, mic = now, locate(env["PW_DUMP"], usb_id)
            with lock:
                storm.watch(mic[1] if mic else None)
        with lock:
            if storm.add(line, now):
                mic = locate(env["PW_DUMP"], usb_id)
                if mic is not None:
                    reset(env, mic)
                    silence.reopened()
                    storm.watch(mic[1])
    sys.exit(proc.wait())


if __name__ == "__main__":
    main()
