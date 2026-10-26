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
    "ACCESS_FINE_LOCATION",
    "ACCESS_COARSE_LOCATION",
    "ACCESS_BACKGROUND_LOCATION",
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
        # The UID-level default is location "foreground only"; force the op
        # allowed so a scan from the service is not treated as background.
        adb.shell_ok("appops", "set", PACKAGE, "android:fine_location", "allow")
        adb.shell_ok("appops", "set", PACKAGE, "android:coarse_location", "allow")
        adb.shell(
            "am", "broadcast", "-p", PACKAGE, "-a", ACTION,
            "--es", "room", presence.room,
            "--es", "router_url", presence.routerUrl,
            "--es", "beacons", json.dumps(presence.beacons, separators=(",", ":")),
            "--ei", "report_seconds", str(presence.reportSeconds),
            "--ei", "window_seconds", str(presence.windowSeconds),
        )
        # iBeacon adverts are location data: without neverForLocation (which
        # would filter iBeacons out) Android returns no scan results unless
        # location is on. Both forms, since `cmd location` is newer.
        adb.shell_ok("cmd", "location", "set-location-enabled", "true")
        adb.shell_ok("settings", "put", "secure", "location_mode", "3")
        # Exempt from background limits: a boot-time foreground service and a
        # long-running BLE scan are exactly what Android otherwise blocks.
        adb.shell_ok("cmd", "deviceidle", "whitelist", "+" + PACKAGE)
        # A broadcast cannot reliably start a foreground service from the
        # background on Android 12+, and `am` from adb can, so start it here.
        adb.shell("am", "start-foreground-service",
                  "-n", f"{PACKAGE}/.PresenceService")
    except AdbError as exc:
        return Outcome("presence", PACKAGE, FAILED, str(exc))

    adb.write_marker(marker)
    return Outcome("presence", PACKAGE, CHANGED, f"configured {presence.room}")
