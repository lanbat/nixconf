"""Manifest schema. Nix writes it; the runner reads it."""
from __future__ import annotations

import json
from dataclasses import dataclass

NAMESPACES = ("global", "secure", "system")


class ManifestError(Exception):
    """The manifest is malformed. Exit code 4."""


@dataclass(frozen=True)
class Apk:
    packageId: str
    versionCode: int
    versionName: str
    minSdk: int
    path: str
    source: str


@dataclass(frozen=True)
class CaCert:
    name: str
    sha256: str
    path: str


@dataclass(frozen=True)
class Obtainium:
    path: str
    sha256: str


@dataclass(frozen=True)
class DeviceOwner:
    enable: bool
    component: str | None


@dataclass(frozen=True)
class Presence:
    room: str
    routerUrl: str
    beacons: dict[str, str]  # uuid -> person key
    reportSeconds: int
    windowSeconds: int


@dataclass(frozen=True)
class Manifest:
    device: str
    host: str
    port: int
    abi: str
    allowDowngrade: bool
    apks: list[Apk]
    caCerts: list[CaCert]
    settings: dict[str, dict[str, str]]
    obtainium: Obtainium | None
    deviceOwner: DeviceOwner
    homeActivity: str | None = None
    presence: Presence | None = None


def load(path: str) -> Manifest:
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc

    try:
        settings: dict[str, dict[str, str]] = {}
        for ns, table in (raw.get("settings") or {}).items():
            if ns not in NAMESPACES:
                raise ManifestError(
                    f"unknown settings namespace {ns!r}; expected one of {', '.join(NAMESPACES)}"
                )
            settings[ns] = {k: _as_setting_value(v) for k, v in table.items()}

        owner_raw = raw.get("deviceOwner") or {"enable": False, "component": None}
        owner = DeviceOwner(bool(owner_raw.get("enable")), owner_raw.get("component"))
        if owner.enable and not owner.component:
            raise ManifestError("deviceOwner.enable is set but component is missing")

        obtainium_raw = raw.get("obtainium")
        obtainium = (
            Obtainium(obtainium_raw["path"], obtainium_raw["sha256"]) if obtainium_raw else None
        )

        return Manifest(
            device=raw["device"],
            host=raw["host"],
            port=int(raw["port"]),
            abi=raw["abi"],
            allowDowngrade=bool(raw.get("allowDowngrade", False)),
            apks=[Apk(**a) for a in raw.get("apks", [])],
            caCerts=[CaCert(**c) for c in raw.get("caCerts", [])],
            settings=settings,
            obtainium=obtainium,
            deviceOwner=owner,
            homeActivity=raw.get("homeActivity"),
            presence=Presence(**raw["presence"]) if raw.get("presence") else None,
        )
    except ManifestError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ManifestError(f"malformed manifest {path}: {exc}") from exc


def _as_setting_value(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)
