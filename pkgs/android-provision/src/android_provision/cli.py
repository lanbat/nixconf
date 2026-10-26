"""Five phases: connect, plan, apply, verify, report.

One failing resource never aborts the run -- everything independent still
converges, and the exit code reports the failure at the end.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from .adb import Adb, AdbError, DeviceInfo, DeviceOffline, DeviceUnauthorized
from .manifest import ManifestError, load
from .outcome import FAILED, Outcome
from .resources import apks, cacerts, device_owner, home, obtainium, presence, settings

EXIT_OK = 0
EXIT_RESOURCE_FAILED = 1
EXIT_UNREACHABLE = 2
EXIT_UNAUTHORIZED = 3
EXIT_MANIFEST = 4

# home after apks: the launcher must be installed before it can be the default.
# presence after apks: the app must be installed before it can be configured.
# Device Owner last: the DPC package must be installed before dpm can name it.
RESOURCES = (apks, presence, home, settings, cacerts, obtainium, device_owner)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="android-provision")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("provision", "converge the device to the manifest"),
        ("plan", "show what provision would change, without touching the device"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--manifest", required=True)
        if name == "provision":
            p.add_argument(
                "--force", action="store_true",
                help="re-apply marker-backed resources (CA certs, Obtainium) "
                     "and re-apply the home screen even if it already matches",
            )

    u = sub.add_parser("update", help="refresh apks.lock.json from F-Droid and GitHub")
    u.add_argument("--lockfile", required=True)
    u.add_argument("--fdroid", action="append", default=[], metavar="PACKAGE_ID")
    u.add_argument("--github", action="append", default=[], metavar="REPO=GLOB")

    c = sub.add_parser("capture", help="snapshot the device's apps, settings and home screen")
    c.add_argument("--manifest", required=True)
    c.add_argument("--out-dir", required=True)
    c.add_argument("--lockfile", default=None, help="apks.lock.json for the app report")
    c.add_argument("--no-fdroid", action="store_true", help="don't look apps up on F-Droid")
    c.add_argument("--diff", metavar="OLD", help="also compare with an earlier snapshot")
    c.add_argument("--ignore", action="append", default=[], metavar="NS/KEY")

    df = sub.add_parser("diff", help="compare two snapshots; prints a restore fragment")
    df.add_argument("old")
    df.add_argument("new")
    df.add_argument("--ignore", action="append", default=[], metavar="NS/KEY")
    return parser


def report(outcomes: list[Outcome]) -> None:
    for o in outcomes:
        line = f"  {o.status:8} {o.resource}/{o.target}"
        if o.reason:
            line += f" -- {o.reason}"
        print(line)


def _connect(manifest) -> tuple[Adb, DeviceInfo] | int:
    """Connect, or return the exit code that explains why not."""
    adb = Adb(manifest.host, manifest.port)
    try:
        return adb, adb.connect()
    except DeviceUnauthorized as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNAUTHORIZED
    except DeviceOffline as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNREACHABLE
    except (AdbError, subprocess.TimeoutExpired) as exc:
        # Anything else adb-shaped -- a box that drops Wi-Fi mid-run, returns
        # junk, or times out -- must never surface as a bare traceback.
        print(
            f"error: {manifest.host}:{manifest.port} did not respond as expected: {exc}",
            file=sys.stderr,
        )
        return EXIT_UNREACHABLE


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "update":
        return _update(args)
    if args.command == "capture":
        return _capture(args)
    if args.command == "diff":
        return _diff(args)

    apply = args.command == "provision"
    force = getattr(args, "force", False)

    try:
        manifest = load(args.manifest)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_MANIFEST

    connected = _connect(manifest)
    if isinstance(connected, int):
        return connected
    adb, info = connected

    verb = "provisioning" if apply else "planning"
    print(f"{verb} {manifest.device} ({manifest.host}:{manifest.port}) "
          f"-- {info.model}, SDK {info.sdk}")

    outcomes: list[Outcome] = []
    for module in RESOURCES:
        outcomes.extend(module.reconcile(adb, info, manifest, apply=apply, force=force))

    report(outcomes)

    failed = [o for o in outcomes if o.status == FAILED]
    if failed:
        print(f"{len(failed)} resource(s) failed", file=sys.stderr)
        return EXIT_RESOURCE_FAILED
    return EXIT_OK


def _update(args) -> int:
    import os

    from . import update as updater

    token = os.environ.get("GITHUB_TOKEN")
    entries = []
    try:
        if args.fdroid:
            index = updater.fetch_json(updater.FDROID_INDEX)
            for package_id in args.fdroid:
                entries.append(updater.resolve_fdroid(index, package_id))
        for spec in args.github:
            repo, _, glob = spec.partition("=")
            release = updater.fetch_json(updater.GITHUB_API.format(repo=repo), token)
            apk = updater.fetch_bytes(updater.select_asset(release, glob)["browser_download_url"])
            entries.append(updater.resolve_github(release, repo, glob, apk))
    except updater.ResolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_MANIFEST

    updater.write_lockfile(args.lockfile, entries)
    print(f"wrote {len(entries)} entries to {args.lockfile}")
    return EXIT_OK


def _load_snapshot_or_report(snapshot_module, path: str) -> dict | int:
    """Load a snapshot, or print the error and return the exit code that explains why not."""
    try:
        return snapshot_module.load(path)
    except snapshot_module.SnapshotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_MANIFEST


def _print_diff(diff_module, old: dict, new: dict, ignore: frozenset[str] = frozenset()) -> None:
    result = diff_module.compare(old, new, ignore)
    print(diff_module.render(result), end="")
    print(diff_module.restore_fragment(result), end="")


def _capture(args) -> int:
    from . import diff, snapshot

    try:
        manifest = load(args.manifest)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_MANIFEST
    connected = _connect(manifest)
    if isinstance(connected, int):
        return connected
    adb, info = connected

    try:
        snap = snapshot.take(adb, info, device=manifest.device)
    except (AdbError, subprocess.TimeoutExpired) as exc:
        # The box answered `adb connect` but stopped responding partway
        # through the capture (Wi-Fi drop, reboot, ...). This unit runs
        # unattended, so it must exit cleanly rather than raise.
        print(
            f"error: {manifest.host}:{manifest.port} did not respond as expected: {exc}",
            file=sys.stderr,
        )
        return EXIT_UNREACHABLE

    try:
        path = snapshot.save(snap, args.out_dir)
    except OSError as exc:
        print(f"error: cannot write snapshot: {exc}", file=sys.stderr)
        return EXIT_RESOURCE_FAILED

    print(f"snapshot of {manifest.device} written to {path}")

    from . import sources, update

    # The snapshot is already saved above; nothing from here on -- a bad
    # lockfile, an unreachable F-Droid index, or a malformed lockfile entry --
    # may fail the unit. This block is advisory only.
    try:
        try:
            lock = sources.load_lock(args.lockfile or sources.default_lockfile())
        except (OSError, ValueError) as exc:
            # A missing or unreadable lockfile (e.g. running from source
            # without --lockfile) must not fail the capture -- just skip the
            # app-source report.
            print(f"warning: cannot read lockfile ({exc}); skipping app source report",
                  file=sys.stderr)
        else:
            index = None
            if not args.no_fdroid:
                try:
                    index = update.fetch_json(update.FDROID_INDEX)
                except (OSError, ValueError) as exc:
                    print(f"warning: F-Droid index unavailable ({exc}); unlocked apps show as unknown",
                          file=sys.stderr)
            print(sources.fragment(sources.propose(snap["packages"], lock, index)), end="")
    except Exception as exc:
        print(f"warning: app report skipped: {exc}", file=sys.stderr)

    if args.diff:
        old = _load_snapshot_or_report(snapshot, args.diff)
        if isinstance(old, int):
            return old
        _print_diff(diff, old, snap, frozenset(args.ignore))

    return EXIT_OK


def _diff(args) -> int:
    from . import diff, snapshot

    old = _load_snapshot_or_report(snapshot, args.old)
    if isinstance(old, int):
        return old
    new = _load_snapshot_or_report(snapshot, args.new)
    if isinstance(new, int):
        return new
    _print_diff(diff, old, new, frozenset(args.ignore))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
