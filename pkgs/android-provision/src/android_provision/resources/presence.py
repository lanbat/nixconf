"""Configure the android-presence app: grant its BLE permissions and push the
deployment's room/router/beacons.

The app is a generic room sensor; this is the only device-specific data it
needs. The beacon map is the deployment's people's `bleBeacon` UUIDs, so it
follows the people config without a second list to keep in step.
"""
from __future__ import annotations

import hashlib
import json

from ..adb import Adb, AdbError, DeviceInfo
from ..manifest import Manifest, Presence
from ..outcome import CHANGED, FAILED, OK, SKIPPED, Outcome

PACKAGE = "com.lanbat.presence"
ACTION = "com.lanbat.presence.CONFIG"
PERMISSIONS = (
    "BLUETOOTH_SCAN",
    "BLUETOOTH_CONNECT",
    "POST_NOTIFICATIONS",
    "ACCESS_FINE_LOCATION",  # Android 11 and below; harmless if undeclared/granted
)


def reconcile(
    adb: Adb, info: DeviceInfo, manifest: Manifest, *, apply: bool, force: bool
) -> list[Outcome]:
    if manifest.presence is None:
        return []
    return [_one(adb, manifest.presence, apply=apply, force=force)]


def _marker(presence: Presence) -> str:
    raw = json.dumps(
        [presence.room, presence.routerUrl, presence.beacons,
         presence.reportSeconds, presence.windowSeconds],
        sort_keys=True,
    )
    return "presence/" + hashlib.sha256(raw.encode()).hexdigest()


def _one(adb: Adb, presence: Presence, *, apply: bool, force: bool) -> Outcome:
    from .apks import installed_version  # local: apks imports the manifest too

    if installed_version(adb, PACKAGE) is None:
        return Outcome("presence", PACKAGE, SKIPPED, "app is not installed")

    marker = _marker(presence)
    if adb.marker_exists(marker) and not force:
        return Outcome("presence", PACKAGE, OK)
    if not apply:
        return Outcome("presence", PACKAGE, CHANGED, f"would configure {presence.room}")

    try:
        for permission in PERMISSIONS:
            # Ignore failures: a permission the device/OS does not have simply
            # does not apply (e.g. POST_NOTIFICATIONS below Android 13).
            adb.shell_ok("pm", "grant", PACKAGE, "android.permission." + permission)
        adb.shell(
            "am", "broadcast", "-a", ACTION,
            "--es", "room", presence.room,
            "--es", "router_url", presence.routerUrl,
            "--es", "beacons", json.dumps(presence.beacons, separators=(",", ":")),
            "--ei", "report_seconds", str(presence.reportSeconds),
            "--ei", "window_seconds", str(presence.windowSeconds),
        )
        # A broadcast cannot reliably start a foreground service from the
        # background on Android 12+, and `am` from adb can, so start it here.
        adb.shell("am", "start-foreground-service",
                  "-n", f"{PACKAGE}/.PresenceService")
    except AdbError as exc:
        return Outcome("presence", PACKAGE, FAILED, str(exc))

    adb.write_marker(marker)
    return Outcome("presence", PACKAGE, CHANGED, f"configured {presence.room}")
