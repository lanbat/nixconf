# android-presence

A small Android TV app that makes a TV box a **Bluetooth room sensor** for the
assistant router, the same job `pkgs/room-presence` does on a Pi: it scans for
the household's phone beacons and reports their median RSSI to the router, so
`follow-me` can place a person in that room and play to the room's player.

It is not part of the NixOS build. Like the other provisioned apps it is built
as an APK and installed over ADB by `pkgs/android-provision`; the provisioner
also pushes this deployment's configuration.

## What it does

- A foreground service (`PresenceService`) scans BLE continuously
  (`BluetoothLeScanner`, low latency) and decodes iBeacon adverts
  (`BeaconParser`: Apple company id `0x004C`, `02 15 <uuid> …`). The scan is
  filtered to those adverts: Android refuses *unfiltered* scans while the
  screen or location is off, which is the normal state of a TV box.
- `BeaconTracker` keeps a 20 s window per beacon and reports the **median**
  RSSI every 2 s — the same shape as `pkgs/room-presence/room_presence.py`, so
  the router treats it identically.
- `RouterClient` opens `ws://<router>:<port>/v1/body` and speaks the room-sensor
  protocol:
  ```json
  {"hello":{"room":"Living Room","kind":"room-sensor","proto":1}}
  {"seen":[{"person":"momchilov","rssi":-70}]}
  ```
  The router drops `rssi < -85` and non-`int` values itself.
- `ConfigReceiver` takes the deployment config from a broadcast (below) and
  saves it; `BootReceiver` restarts the service after a reboot or update. The
  provisioner starts the service over adb, which avoids Android 12+'s
  background foreground-service restriction.

## Configuration (the provisioner sends this)

```bash
adb shell am broadcast -p com.lanbat.presence -a com.lanbat.presence.CONFIG \
  --es room "Living Room" \
  --es router_url "ws://192.168.1.10:8770/v1/body" \
  --es beacons '{"5affd0d5-9828-435c-b177-4221519517e0":"momchilov"}'
adb shell am start-foreground-service -n com.lanbat.presence/.PresenceService
```

The map is `uuid -> person key`; dashes and case in the UUID are optional.
`-p` matters: a manifest receiver does not get an implicit broadcast on
Android 8+, so the intent must name the package.
The second command is needed because Android 12+ refuses a foreground service
started from the background by a broadcast; `am` from adb is allowed.
`presence` (the provisioner resource) sends both commands for you.

Optional: `--ei report_seconds 2 --ei window_seconds 20`.

Runtime permissions the provisioner grants:

```bash
adb shell pm grant com.lanbat.presence android.permission.BLUETOOTH_SCAN
adb shell pm grant com.lanbat.presence android.permission.BLUETOOTH_CONNECT
adb shell pm grant com.lanbat.presence android.permission.ACCESS_FINE_LOCATION
adb shell pm grant com.lanbat.presence android.permission.ACCESS_COARSE_LOCATION
adb shell pm grant com.lanbat.presence android.permission.ACCESS_BACKGROUND_LOCATION
adb shell pm grant com.lanbat.presence android.permission.POST_NOTIFICATIONS   # Android 13+
adb shell appops set com.lanbat.presence android:fine_location allow
adb shell appops set com.lanbat.presence android:coarse_location allow
adb shell cmd location set-location-enabled true
adb shell cmd deviceidle whitelist +com.lanbat.presence
```

The background/coarse location and `location` foreground-service type are what
make a scan from the service count as location access on Android 14 rather than
background access. The provisioner does all of the above; the filtered scan then
works even when the box later turns the screen or location off, but the
permissions have to be granted.

One catch: `usesPermissionFlags="neverForLocation"` is deliberately **not**
set — Android then filters iBeacons out of the results. That is also why
`ACCESS_FINE_LOCATION` is needed on every Android version, not just 11 and
below.

## Pure logic, verified without the Android SDK

`verify/CoreTest.java` exercises `BeaconParser` and `BeaconTracker` with plain
`java`, no Android SDK or Gradle needed:

```bash
mkdir -p /tmp/ap-build
javac -d /tmp/ap-build \
  app/src/main/java/com/lanbat/presence/BeaconParser.java \
  app/src/main/java/com/lanbat/presence/BeaconTracker.java \
  verify/CoreTest.java
java -cp /tmp/ap-build com.lanbat.presence.CoreTest
```

## Building the APK

Needs an Android SDK (compileSdk 34) and Gradle 8.7+ on `PATH` (or add a
wrapper):

```bash
cd pkgs/android-presence
gradle :app:assembleDebug       # app/build/outputs/apk/debug/app-debug.apk
gradle :app:testDebugUnitTest   # the JUnit tests in app/src/test
```

A debug APK installs and runs as-is. A release build needs a signing config;
sign it with the deployment keystore in CI, publish the APK as a GitHub release
asset, and pin it in `pkgs/android-provision/apks.lock.json` via a `github`
source, like the suite's other apps. A debug-signed install must be removed
(`adb uninstall com.lanbat.presence`) before a differently signed release can
replace it.

## Notes / limits

- Android TV may still kill background apps on some boxes; the foreground
  service + wake lock is the best the platform offers. Verify the box's BLE and
  background behaviour before relying on it.
- The service **re-registers its BLE scan every 60 s**. Android can stop
  delivering scan results without telling the app — a standby transition, or
  location being turned off — and the old registration then keeps looking
  alive. The periodic restart is what makes the app recover on its own; it
  also logs `location services are off` while that is the case, because
  Android then delivers no iBeacon results at all.
- **Accept the “Turn on location with Location Accuracy?” prompt** on the box.
  That is Google Play Services' network-location consent. Until it is accepted,
  Play Services switches location off whenever the display sleeps, and Android
  then delivers no BLE scan results at all; once accepted, location stays on
  across sleep and the box keeps scanning with the TV off. A factory reset (or a
  Play Services update that revokes it) brings the prompt back — the app logs
  `location services are off` while the consent is missing.
- The phone's iBeacon must be advertising (Companion app, battery optimisation
  off). The app cannot fix a phone that stops advertising.
- The router socket is plain `ws://`, so the manifest sets
  `usesCleartextTraffic="true"`; without it targetSdk 28+ blocks the connection.
