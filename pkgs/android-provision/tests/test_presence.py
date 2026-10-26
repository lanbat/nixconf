from android_provision.adb import Adb
from android_provision.manifest import DeviceOwner, Manifest, Presence
from android_provision.resources import presence as presence_resource

PRESENCE = Presence(
    room="Living Room",
    routerUrl="ws://192.0.2.10:8770/v1/body",
    beacons={"5affd0d5-9828-435c-b177-4221519517e0": "momchilov"},
    reportSeconds=2,
    windowSeconds=20,
)


def make_manifest(presence):
    return Manifest(
        device="bedroom", host="192.0.2.50", port=5555, abi="arm64-v8a",
        allowDowngrade=False, apks=[], caCerts=[], settings={},
        obtainium=None, deviceOwner=DeviceOwner(False, None),
        presence=presence,
    )


def run(device, presence, *, apply=True):
    adb = Adb("192.0.2.50", 5555)
    info = adb.connect()
    return presence_resource.reconcile(
        adb, info, make_manifest(presence), apply=apply, force=False
    )


def test_no_presence_does_nothing(device):
    assert run(device, None) == []


def test_not_installed_is_skipped(device):
    outcomes = run(device, PRESENCE)
    assert outcomes[0].status == "skipped"
    assert "not installed" in outcomes[0].reason


def test_configures_with_the_room_and_beacons(device):
    device.state["packages"]["com.lanbat.presence"] = 1
    device.commit()

    outcomes = run(device, PRESENCE)

    assert [o.status for o in outcomes] == ["changed"]
    state = device.reload()
    broadcast = state["broadcasts"][-1]
    assert "com.lanbat.presence.CONFIG" in broadcast
    assert "Living Room" in broadcast
    assert "ws://192.0.2.10:8770/v1/body" in broadcast
    assert any("momchilov" in arg for arg in broadcast)
    # adb is allowed to start the foreground service even on Android 12+.
    assert any("PresenceService" in arg for arg in state["services"][-1])
    granted = {grant[1] for grant in state["grants"]}
    assert "android.permission.BLUETOOTH_SCAN" in granted
    assert "android.permission.BLUETOOTH_CONNECT" in granted


def test_the_same_configuration_twice_is_ok(device):
    device.state["packages"]["com.lanbat.presence"] = 1
    device.commit()

    run(device, PRESENCE)
    outcomes = run(device, PRESENCE)

    assert [o.status for o in outcomes] == ["ok"]


def test_plan_changes_nothing(device):
    device.state["packages"]["com.lanbat.presence"] = 1
    device.commit()

    outcomes = run(device, PRESENCE, apply=False)

    assert [o.status for o in outcomes] == ["changed"]
    assert "broadcasts" not in device.reload()
