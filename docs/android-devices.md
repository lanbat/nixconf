# Android device provisioning

`androidDevices` declaratively provisions Android TV boxes over ADB from the server:
pinned APKs, an internal CA certificate, an Obtainium tracking list and `settings put`
values, converged by a systemd oneshot unit per box. Nothing runs on a timer — a box is
touched only when you ask, never while someone is watching TV on it.

## Limitations, before anything else

Android's platform model — not this tool — sets these limits. They were verified
against the reference device (a Homatics Box R 4K Plus, Android 14 / SDK 34, unrooted
user build) and hold for any similar unrooted box.

| Limit | Consequence |
|---|---|
| Since Android 7, apps ignore user-installed CAs unless they opt in | Where the internal CA can be installed, that fixes the **browser**, not Kodi, Jellyfin or YouTube. Where it can't (next rows), see [Apps that can't trust the internal CA](#apps-that-cant-trust-the-internal-ca-jellyfin-clients). |
| No `adb` command installs a CA silently on an unrooted user build | Below Android 11, one on-screen "Install this certificate?" confirmation per box, every time the CA changes. |
| From Android 11 the CA install dialog can't be opened from `adb`; CA certificates install only from Settings | The provisioner copies the CA to `/sdcard/Download/` and launches nothing. The reference box (Android 14) has only Android TV's Settings, with **no certificate screen**, so its user trust store can't take the CA at all: only apps with their own certificate import (Argosy) can use it. |
| The trust store (`/data/misc/user/0/cacerts-added/`) can't be read without root | Obtainium idempotency, and the CA's below Android 11, is **marker-backed** — a file the tool wrote to `/sdcard/.lanbat-provision/` last time, not proof the device still agrees. `provision --force` re-applies regardless of the marker. |
| Google Play-only apps can't be provisioned by any of the three sources (F-Droid, GitHub releases, Obtainium) | Roughly 10 of the 24 apps on the reference box (Stremio, Twitch, Castify, Projectivy Launcher, Nova BG among them). Unsupported by design, not a bug — put these on Obtainium's list if it can track them, or install them by hand. |
| The reference box has no DocumentsUI | Every Storage Access Framework file picker fails, so Obtainium's file-based list import doesn't work either. The provisioner pushes the URL list to `/sdcard/Download/` and prints it; you paste it into Obtainium's "Import from URL list" text field by hand. |
| Device Owner mode requires a box with **no configured accounts** | In practice, a factory reset per box. The tool will never perform one for you — `deviceOwner.enable` only calls `dpm set-device-owner` and reports `failed` with the reason if an account is already configured. |
| Split APKs (Android App Bundle install sets) aren't supported | `adb install` takes exactly one file. A GitHub release that publishes a base APK plus config splits either fails to resolve during `android-update` (the asset glob matches more than one file) or, if narrowed to one file, fails to install on the device and is reported `failed`, not silently skipped. |
| The module never uninstalls anything | Removing an app from `packages`/`github`/`obtainium` removes nothing from the device. Uninstall it on the box yourself. |

If a limitation here is a dealbreaker for an app, that app belongs on Obtainium's list
(if it can update it) or stays a manual install — not a fight with this tool.

## Quickstart

Enable the plugin on the server host and declare a device:

```nix
# deployments/homelab/deploy.nix
hosts.server.plugins = [
  inputs.self.lanbatPlugins.services
  inputs.self.lanbatPlugins.android
];

deployment.androidDevices = {
  bedroom = {
    host = "192.0.2.50"; # ADB over TCP must already be enabled on the box
    packages = [ "de.badaix.snapcast" ];
  };
};
```

`lanbatPlugins.android` configures nothing when `androidDevices` is empty, so it's safe
to enable on every server even before the first box is declared.

Then, per box and in this order: authorize ADB once (next section), run the `-plan` unit
and read what it would change, take a baseline with `android-capture-<box>`
([Snapshots](#snapshots-and-restoring-after-a-reset)), and run
`android-provision-<box>`. Set `abi` to what the box reports: many TV boxes run 32-bit
Android (see [RomM through Argosy](#romm-through-argosy)).

## One-time ADB authorization

Before the server can reach a box, do this once per box:

1. On the box, enable developer options (Settings → About → press **Build** seven
   times), then turn on **USB debugging** in them. What the server needs is `adbd`
   listening on TCP port 5555; check from the server with
   `nc -z <box> 5555`. The reference box, connected by Ethernet, listened there.
   **Wireless debugging** (Android 11+, pairing codes) is Wi-Fi only and not needed.
2. Run the plan unit once: `systemctl start android-provision-bedroom-plan`. It fails
   with exit code 3 (`… has not authorized this key; accept the on-screen 'Allow USB
   debugging?' dialog`), and the box shows that dialog.
3. Accept it with **Always allow from this computer**, then run the plan unit again. It
   now lists what a provisioning run would change, without changing anything.

`adb`'s client key lives at `/var/lib/android-provision/.android/adbkey` (the unit sets
`HOME=/var/lib/android-provision` so `adb` creates and reuses the key there) and is
**never rotated**. That's deliberate: a key regenerated per run would re-trigger the
dialog on the TV every single time. Losing that key file (state directory deleted,
reinstall) means re-accepting the dialog on every box once.

A box's `provision`, `plan` and `capture` units all share the one `adb` client server
under `HOME=/var/lib/android-provision`, so running two of them against the same box at
once can make one drop mid-run (`EXIT_UNREACHABLE`, exit 2). Run a box's units one at a
time.

## Running it

Three ways to run a device's manifest, all equivalent up to whether they touch the box
and where the manifest comes from:

```bash
systemctl start android-provision-bedroom         # converge the device
systemctl start android-provision-bedroom-plan     # dry run: connects, reports, changes nothing
nix run .#android-provision -- provision --manifest <path>  # run a manifest directly
```

The `-plan` unit runs the same reconciliation with `apply=false`: it still connects to
the box and reads its state, but every resource that would change reports `changed`
with a "would ..." reason instead of touching anything. Use it before `provision` on a
box you don't want to risk mid-show.

`nix run .#android-provision` is the raw CLI — useful for testing a hand-written
manifest or scripting outside the NixOS units. `provision` also accepts `--force`,
which re-applies the marker-backed resources (CA certs, Obtainium's URL list) even if
their marker says already done, and re-applies the home screen even if it already
matches `homeActivity`.

### Outcomes

Every resource (an APK, a setting, the CA cert, the Obtainium list, the device owner)
reports one of four outcomes, printed as `<status> <resource>/<target> -- <reason>`:

| Status | Meaning |
|---|---|
| `ok` | Already matches the manifest; nothing to do. |
| `changed` | Applied (or, under `plan`, would be applied). |
| `skipped` | Deliberately not applied, with a reason — e.g. `minSdk` too high for the device, or a newer version is already installed and `allowDowngrade` is unset. `skipped` exists so an unsupported or unverifiable situation is reported honestly instead of a false `ok`. |
| `failed` | Attempted and did not work, with a reason. |

One resource failing never aborts the run: every independent resource still converges,
and the process exit code reports the failure at the end (see Exit codes below).

## Default home screen

Setting `homeActivity` makes an installed app the box's default launcher — useful for a
box dedicated to a single app such as Argosy (see "RomM through Argosy" below):

```nix
androidDevices.bedroom.homeActivity = "com.nendo.argosy/.MainActivity";
```

The value is `package/activity`; a leading `.` on the activity (as Android manifests
commonly write it) resolves against the package. The `home` resource runs after `apks`
— the launcher has to be installed before it can be made the default — and does three
things: reads the box's current home activity (`cmd package resolve-activity --brief -a
android.intent.action.MAIN -c android.intent.category.HOME`), calls `cmd package
set-home-activity <component>` only if that differs from what's wanted, then **reads it
back** to confirm the change stuck. A box that refuses (some vendor skins ignore
`set-home-activity` outright, or only accept the change from the on-screen launcher
picker) reports `failed` with `adb`'s reply — never a silent no-op, and never `ok` for a
change that didn't actually take.

## Updating apps

APK sources are pinned in `pkgs/android-provision/apks.lock.json`, resolved once by a
network-touching update step so that evaluating the flake stays pure and a provision
run never depends on F-Droid or GitHub being reachable. Never hand-edit the lockfile.

```bash
nix run .#android-update -- "" \
  --fdroid de.badaix.snapcast \
  --fdroid io.github.sds100.keymapper \
  --github 'rommapp/argosy-launcher=argosy-v*.[0-9].apk'
```

Review the diff (`git diff pkgs/android-provision/apks.lock.json`) before committing —
`android-update` **replaces the entire lockfile** with only the packages and GitHub
repos you pass it that run. The command above must list every entry that should still
be in the lockfile afterwards, not just the one you meant to refresh: omit an app you
meant to keep and the diff will show it disappearing; that's your signal to add it back
to the command, not evidence of a bug. The empty first argument selects the default
lockfile path; pass a path there instead to update a different file.

`GITHUB_TOKEN` in the environment raises the GitHub API rate limit for `--github`
lookups; it isn't required for public repos at low volume.

F-Droid moves a superseded APK from `/repo/` to `/archive/` once an app publishes a new
version, so a pinned `/repo/` URL in the lockfile 404s at that point — with no warning
and no relation to anything you changed. The symptom is an opaque `fetchurl` 404 on
*any* rebuild of a host that enables this plugin, F-Droid apps and all, not just the one
that moved. The fix is the same either way: `nix run .#android-update` to re-resolve the
pinned URLs, then commit the regenerated lockfile.

## Snapshots and restoring after a reset

Every device also gets an `android-capture-<name>` oneshot unit — not started
automatically, only ever on request:

```bash
systemctl start android-capture-bedroom
```

It takes a **read-only** snapshot: every user-installed package with its version and
installer (`pm list packages -3 -i --show-versioncode`), every `settings list
global|secure|system` key and value, the current home activity, and the device facts
(model, SDK, ABI) `adb` already reports on connect. The snapshot is written to
`/var/lib/android-provision/<name>/snapshots/<UTC timestamp>.json`, mode `0600` in a
mode `0700` directory, and it **never enters the repository**: settings values include
things like the device's Bluetooth address and Wi-Fi network name, which have no
business in a public git history. Keep snapshots on the server's disk (or copy them
somewhere private) — don't `git add` them.

### The app report

Right after a capture, it prints a proposed source for every installed app, as a
ready-to-paste fragment for `androidDevices.<box>`:

- already pinned in the lockfile (as a `packages` or `github` entry) — reused as-is;
- found in F-Droid's index but not yet pinned — added under `packages` with a comment
  that it still needs `nix run .#android-update`;
- installed by the Play Store (`com.android.vending`) — printed as a comment, for
  reinstalling by hand and signing in;
- anything else — printed as "no known source", meaning it needs a GitHub repo or an
  Obtainium URL added by hand.

`capture --no-fdroid` skips the F-Droid index lookup (the report then treats an unlocked
app as unknown rather than checking F-Droid); `capture --lockfile PATH` reports against
a lockfile other than the one built into the package.

### Comparing two snapshots

`android-provision diff OLD NEW [--ignore NS/KEY]` compares two snapshot files and
prints, per settings namespace, every key that changed (`old -> new`), apps that
appeared or disappeared, an app whose version changed, any home-activity change, and —
last — a ready-to-paste

```nix
settings = { ... };
homeActivity = "...";
```

fragment holding the *old* value of every setting that differs (the values to restore).
A setting whose old value is the literal string `"null"` is listed as changed but never
put in the restore fragment as a value — `settings put ... null` would store that literal
string, not leave the key unset — so it appears there as a one-line comment instead.
`capture --diff OLD [--ignore NS/KEY]` runs the same comparison, with the same
`--ignore`, between `OLD` and the snapshot the capture just took, so a single command
can both snapshot a freshly reset box and show what the reset changed.

A handful of keys change by themselves or identify one installation — boot counters,
setup-wizard flags, the Bluetooth address, `secure/android_id` — and restoring them is
meaningless. `diff.VOLATILE` lists them with a one-line reason each; `--ignore NS/KEY`
(repeatable) adds more without touching the code, for anything volatile a real device
turns up that the built-in list doesn't cover yet.

### The reset runbook

To learn exactly what a factory reset costs a given box, and turn that into
`androidDevices.<box>` configuration:

1. Before touching the box, take a baseline: `systemctl start android-capture-<box>`.
2. Factory-reset the box. Re-enable network ADB and accept the "Allow USB debugging?"
   dialog again — this manual step is irreducible; nothing on the server side can do it
   for you.
3. Snapshot the fresh box: `systemctl start android-capture-<box>`. Find the two
   snapshots with `ls -t /var/lib/android-provision/<box>/snapshots` (newest first) and
   compare them: `android-provision diff <baseline> <newest>`. Copy the settings, apps
   and home activity the diff prints into `androidDevices.<box>`, and extend the ignore
   list with anything that turns out to be volatile.
4. Deploy and provision the box, snapshot it again the same way, and diff the new
   snapshot against the baseline once more. What's left in that diff is what a reset
   really costs on top of provisioning — record it in this document.

Prefer the unit over a manual `android-provision capture` in a root shell: a root shell
has its own `$HOME` (typically `/root`), so `adb` creates and uses a *different* client
key there than the unit's, which re-triggers the "Allow USB debugging?" dialog on the
box and can leave a stray `adb` server running that a later unit run then attaches to.
If a manual capture is unavoidable, prefix it with `HOME=/var/lib/android-provision` to
reuse the unit's key, and find the manifest path the unit passes with `systemctl cat
android-capture-<box>`.

Beyond that runbook, the manual steps a reset can never avoid are: enable network ADB
and accept the ADB prompt, pair Argosy with one code (see below), and sign in to Play
Store apps. Everything else — apps, changed settings, the home screen, the internal CA
file — comes back from one run of the box's provisioning unit. On Android 11 and later
the CA itself then has to be installed from Settings or imported in each app again.

## RomM through Argosy

[Argosy Launcher](https://github.com/rommapp/argosy-launcher) is a RomM client for
Android TV: it lists the RomM library, downloads a game on demand, launches it, and
syncs its save back to RomM. Getting it onto a box is ordinary `androidDevices`
configuration, not a special case:

```nix
androidDevices.bedroom = {
  github = [
    { repo = "rommapp/argosy-launcher"; asset = "argosy-v*.[0-9].apk"; }
  ];
  homeActivity = "com.nendo.argosy/.MainActivity";
  abi = "armeabi-v7a"; # what the box reports; see below
  # caCerts left at its default: the internal CA, delivered to Download for
  # Argosy's own certificate import (see below).
};
```

The pinned asset glob (`argosy-v*.[0-9].apk`) matches Argosy's universal release,
`argosy-v<version>.apk`, and not the per-ABI `argosy-v2.27.3-arm32.apk` or
`argosy-v2.27.3-arm64.apk`. `apk_metadata` reads every ABI the universal APK contains
(`arm64-v8a` and `armeabi-v7a` among them), so one lockfile entry serves every box. It
has to be one: `update.resolve_github` keys its result by repo, `write_lockfile` builds
`{ key: entry }` and the module looks up `lock.${g.repo}`, so a second `github` entry for
the same repo would replace the first rather than add a variant.

Set the device's `abi` to what the box reports, not what its chip is. Many Android TV
boxes run a 32-bit Android on a 64-bit chip: the reference Homatics Box R 4K Plus reports
only `armeabi-v7a, armeabi`. The plan unit prints the reported list when an APK doesn't
match (`skipped apk/… -- configured abi 'arm64-v8a' not supported by device (device
reports: armeabi-v7a, armeabi)`), and `abi` defaults to `arm64-v8a`.

Setting `homeActivity` to Argosy's launch activity makes it the box's default home
screen, so the box boots straight into the game library. Argosy has to trust the
internal CA that Caddy issues `romm.<domain>`'s certificate from. It trusts user CAs, but
on Android 11 and later the provisioner can't install one (see the limits above), so
`caCerts` at its default only delivers `caddy-ca-root.crt` to `/sdcard/Download/`. Argosy
has its own import for this: in its RomM settings (or on its first-run screen), **Add
Certificate** and pick `Download/caddy-ca-root.crt`; the button then reads "1 certificate
trusted by Argosy". The import is trusted by Argosy alone.

Pairing is the other step this module can't do for you, since Argosy keeps its server
and session in its private data: create an API token in RomM's web UI, then enter its
8-character pairing code in Argosy, with RomM's address. Argosy then talks to RomM's API
with that paired session; how a browser signs in to RomM (its own accounts or Authentik)
doesn't affect it.

A remote's spare button can open Argosy directly: install Key Mapper
(`packages = [ "io.github.sds100.keymapper" ]`), switch its accessibility service on
through `settings.secure.enabled_accessibility_services`
(`io.github.sds100.keymapper/io.github.sds100.keymapper.system.accessibility.MyAccessibilityService`,
with `accessibility_enabled = "1"`), then map the button once in Key Mapper's UI to
"Launch app → Argosy". The setting is a single list for all accessibility services, so
include any others the box already has (the plan run shows the current value before it
changes anything).

## Apps that can't trust the internal CA (Jellyfin clients)

On a box like the reference one, where the internal CA can't be installed (see the limits
at the top), every app without its own certificate import refuses every service Caddy
serves. Wholphin, a Jellyfin client, fails with `SecureConnectionFailed … Unknown SSL
error` (visible in `adb logcat`), and the box's browser can't open any service either.

The workaround is to let that box reach Jellyfin's own plain-HTTP port, 8096, on the
server. Jellyfin already listens on all interfaces; the firewall policy
(`modules/wiring/policy.nix`) drops 8096 for everything but localhost, so the exception is
an `ACCEPT` above that `DROP`. Add it to the server host's `modules` in the site's
`deploy.nix`, reading the address from the box's `androidDevices` entry so it's written
once:

```nix
hosts.server.modules = [
  (
    { config, lib, ... }:
    let
      spec = "INPUT -p tcp --dport 8096 -s ${config.lanbat.deployment.androidDevices.bedroom.host} -j ACCEPT";
    in
    {
      # mkAfter: inserted after the policy's DROP, so it lands above it.
      networking.firewall.extraCommands = lib.mkAfter "iptables -I ${spec}";
      networking.firewall.extraStopCommands = lib.mkAfter "iptables -D ${spec} 2>/dev/null || true";
    }
  )
];
```

Then add the server in the client by hand as `http://<server address>:8096` and sign in
with a Jellyfin account. Only that box can use the port, and its Jellyfin traffic
(including the sign-in) is unencrypted on the LAN; every other device keeps HTTPS through
Caddy. Keep the box's address fixed in the router, since the rule trusts whichever device
holds it.

Discovery can't hand the box that address. Jellyfin answers discovery with the HTTPS URL
the service sets (`JELLYFIN_PublishedServerUrl`), which disables its per-subnet overrides,
and even without it an override for a single device is ignored: Jellyfin applies an
override only when one of the server's own interfaces lies inside its subnet. A
publicly trusted certificate removes the problem at its root — the box would then trust
the HTTPS address discovery already advertises — and is tracked in #111.

## Option reference

Every field of `androidDevices.<name>`, with its default:

| Option | Default | Description |
|---|---|---|
| `enable` | `true` | Whether to provision this device. |
| `host` | — (required) | Device address. ADB over TCP must already be enabled on it. |
| `port` | `5555` | ADB over TCP port. |
| `abi` | `"arm64-v8a"` | Device ABI, used to pick an APK variant for apps (like VLC) that publish one APK per ABI. |
| `packages` | `[ ]` | F-Droid package identifiers, pinned by `apks.lock.json`. |
| `github` | `[ ]` | GitHub releases, pinned by `apks.lock.json`. Each entry is `{ repo, asset }`, `asset` a glob matching exactly one release asset (default `"*.apk"`). |
| `obtainium` | `[ ]` | Apps handed to Obtainium as `{ url }` entries. Obtainium owns their updates; the provisioner never installs them itself. |
| `caCerts` | `[ ../../secrets/caddy-ca-root.crt ]` | CA certificates for the user trust store: below Android 11 pushed and the install dialog opened; from Android 11 delivered to `/sdcard/Download/` for Settings or an app's own import. Setting this **replaces** the default rather than adding to it. |
| `settings` | `{ }` | `settings put` values by namespace (`global`, `secure`, `system`), e.g. `{ global.screen_off_timeout = 600000; }`. |
| `allowDowngrade` | `false` | Replace an installed app that is newer than the lockfile's pinned version. |
| `homeActivity` | `null` | Activity to make the default home screen, as `package/activity`. Set after the apps are installed; a box that refuses is reported `failed`. |
| `deviceOwner.enable` | `false` | Set a Device Owner via `dpm set-device-owner`. Only succeeds on a box with no configured accounts. |
| `deviceOwner.component` | `null` | DPC admin receiver component, e.g. `"com.example.dpc/.AdminReceiver"`. Required when `deviceOwner.enable` is set. |
| `room` | `null` | The Home Assistant area the box is in. The voice assistant controls the TV of the room it hears the request in. |
| `presence` | `null` | Make the box a Bluetooth room sensor for follow-me: `{ routerUrl, reportSeconds = 2, windowSeconds = 20 }`. Needs `room` and the `android-presence` app installed. Scans every person's `bleBeacon` and reports their median RSSI to the assistant router. |
| `apps` | `{ }` | Apps the voice assistant opens, as spoken name → package, e.g. `{ SmartTube = "app.smarttube.fdroid"; }`. List a box's packages with `adb shell pm list packages -3`. |

Evaluation rejects two devices sharing a `host:port`, `deviceOwner.enable` without a
`deviceOwner.component`, a `homeActivity` that isn't of the form `package/activity`, and
any `packages`/`github` entry missing from `apks.lock.json` (with a pointer to
`nix run .#android-update`) or lacking an APK variant for the device's `abi`.

## Home Assistant and the voice assistant

Every enabled device is also added to Home Assistant's Android TV integration over ADB,
with the provisioning key: `android-adbkey-for-home-assistant` copies the key pair to
Home Assistant's `.android/` directory before its post-setup runs, so the box needs no
second authorization. The post-setup puts the box in its `room` and gives the
integration the `apps` as its app list. By voice, in that room:

- "turn the TV on/off" sends the box's power key;
- "open SmartTube", "launch Netflix": the names in `apps`, and no others, so "open the
  blinds" still reaches the blinds;
- "pause", "resume", "stop" and the volume act on whatever plays in the room, the box
  included.

## Snapcast needs no configuration

`de.badaix.snapcast` (Snapdroid) is the one app in the reference inventory that needs
nothing from this module beyond being installed: it self-discovers the server over
mDNS, and a box with it in `packages` is admitted to the snapserver's ports
automatically (Snapcast has no authentication, so they admit only known clients; see
`services/snapcast.nix`). Only over IPv4, though: Snapdroid on the reference box connects over
IPv6, from Android's rotating privacy addresses, so list such a box by MAC in
`lanbat.services.snapcast.settings.clients` (admitted over both families), as you
would a box that got Snapdroid some other way, such as by hand. `services/snapcast.nix` publishes `_snapcast._tcp`/`_snapcast-ctrl._tcp` over
Avahi and binds snapserver to `::` (dual-stack) because Android's mDNS resolver prefers
a host's IPv6 addresses when Avahi publishes them — a v4-only bind gets "connection
refused" from a client that tried the v6 address first. On this deployment Avahi's own
IPv6 publishing is disabled (a setting shared with Samba, see `services/samba.nix`), so
in practice only the LAN IPv4 address goes out over mDNS today; the dual-stack bind is
kept as a defensive measure regardless.

The caveat: a box on a VLAN or Wi-Fi network that blocks multicast will never see the
mDNS advertisement, Snapdroid will find no server, and nothing in this module — or in
`plan`'s output — can detect that from the server side. If a box can't find audio,
check multicast reachability on its network segment before anything else.

## Bluetooth presence (follow-me)

A box with `presence` set and the `android-presence` app installed is a room sensor
exactly like a Pi running `pkgs/room-presence`: it scans for the phones' iBeacons
(`people.<key>.bleBeacon`), reports the median RSSI of each to the assistant router
every `reportSeconds`, and the router places that person in the box's `room`. With
`lanbat.homeAssistant.followMe` enabled, music then follows them to that room's player.
The box needs no beacon list of its own — the module sends every person's `bleBeacon`
with the config.

```nix
androidDevices.bedroom = {
  # ... host, abi, packages as usual ...
  room = "Living Room";
  presence = { routerUrl = "ws://192.168.1.10:8770/v1/body"; };
  github = [ { repo = "lanbat/android-presence"; } ];  # once released and pinned
};
```

The provisioner installs the APK (from `github`), grants `BLUETOOTH_SCAN`,
`BLUETOOTH_CONNECT`, the location permissions (fine, coarse and background) and
notifications, forces the location app-ops allowed, turns location on, exempts the app
from background limits, sends the room/router/beacons as a broadcast, and starts the
foreground service over adb (Android 12+ refuses a background start).
The scan is filtered to iBeacon adverts because Android refuses *unfiltered* scans
while the screen or location is off — the normal state of a TV box — and iBeacons must
not be hidden with `neverForLocation`, which also makes the location permission
necessary on every Android version. All of that is the `presence` resource, so it
shows up in a `plan` and re-runs only when the config or the person list changes.

The router's own IP allowlist (`IPAddressAllow`, `services/assistant-router.nix`) is
generated from the NixOS hosts that consume it, so it also learns every
`androidDevices` entry with `presence`; a box needs no NixOS host entry. The port is
still opened by hand in the deployment, like the Jellyfin rule, because the wiring
policy can only admit hosts it knows:
`iptables -I INPUT -p tcp --dport 8770 -s <box> -j ACCEPT`.

The app's own notes — the parser, the median window, the filter, and how to verify it
without an Android SDK — are in `pkgs/android-presence/README.md`.

`follow-me` accepts a room with an Android presence sensor as a room it can follow
into, so a box is enough presence for its room; no satellite Pi is needed there.

## Exit codes

| Code | Constant | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | `provision`/`plan`: every resource is `ok` or `changed`. `capture`/`diff`: the snapshot (or comparison) was written/printed; this is unaffected by the advisory app report, which only ever warns (see "The app report"). |
| 1 | `EXIT_RESOURCE_FAILED` | `provision`/`plan`: at least one resource reported `failed`. `capture`: the snapshot was taken but couldn't be written to `--out-dir`. |
| 2 | `EXIT_UNREACHABLE` | The device didn't answer (`adb connect` failed), or, for `capture`, stopped answering partway through the capture — check it's powered on and reachable at `host:port`. |
| 3 | `EXIT_UNAUTHORIZED` | The device answered but hasn't authorized this key — accept the on-screen dialog (see One-time ADB authorization). |
| 4 | `EXIT_MANIFEST` | The manifest couldn't be read or parsed, or (for `android-update`) an app identifier couldn't be resolved. `capture --diff`/`diff`: a snapshot file was unreadable or from another schema. |
