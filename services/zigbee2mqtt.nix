# services/zigbee2mqtt.nix
#
# Zigbee2MQTT — Zigbee bridge over MQTT.
#
# Architecture
# ------------
# Z2M owns the Zigbee USB dongle exclusively.  It publishes device state and
# accepts commands on Mosquitto topics under `zigbee2mqtt/`.  Home Assistant
# discovers devices automatically via MQTT discovery (homeassistant: true).
# Do NOT also enable ZHA in Home Assistant — only one service can own the dongle.
#
# Dongle access
# -------------
# udev creates /dev/zigbee (symlink) owned by group "ha".
# The zigbee2mqtt service user is added to that group.
#
# Unplugging the dongle
# ---------------------
# The service is bound to the dongle's device unit rather than started at boot:
# BindsTo stops it cleanly when the dongle disappears, and the device wants it,
# so it starts again when the dongle comes back.  Without this, pulling the
# dongle for a few seconds made Z2M crash and restart until systemd's start
# limit gave up on it, and it stayed down after the dongle was back.
#
# Secret: mosquitto-z2m-pass.age
#   Single line — the plaintext MQTT password for the zigbee2mqtt user.
#   Written into /var/lib/zigbee2mqtt/secret.yaml at service start so the
#   password never appears in /nix/store.
#
# Web UI: zigbee.<domain> (Caddy -> localhost:8099, Authentik forward auth).
#
# Always-on: yes.  No NFS dependency.  Z2M pairing data lives in
# /var/lib/zigbee2mqtt/ on host root.
{
  config,
  pkgs,
  lib,
  utils,
  ...
}:

let
  serialPort = config.services.zigbee2mqtt.settings.serial.port;
  # dev-zigbee.device for /dev/zigbee.  systemd only creates it because the
  # udev rule below tags the device for systemd.
  serialDevice = "${utils.escapeSystemdPath serialPort}.device";
in
{
  lanbat.services.zigbee2mqtt = {
    subdomain = "zigbee";
    port = 8099;
    auth = "forward-auth";
    # Zigbee2MQTT exists to bridge Zigbee onto MQTT, so a broker is not an
    # optional extra the way it is for Frigate or Home Assistant.
    consumes = [ "mosquitto" ];
    # Its broker password, which mosquitto.nix declares.
    readsSecrets = [ "mosquitto-z2m-pass" ];
    dashboard = {
      group = "Automation";
      name = "Zigbee2MQTT";
      description = "Zigbee bridge";
    };
  };

  systemd.services.zigbee2mqtt = {
    after = [
      "mosquitto.service"
      serialDevice
    ];
    requires = [ "mosquitto.service" ];
    # Follow the dongle: stop when it goes, start when it (re)appears.  Not
    # wanted by multi-user.target, so a boot without the dongle neither waits
    # for it nor leaves a failed unit behind.
    bindsTo = [ serialDevice ];
    wantedBy = lib.mkForce [ serialDevice ];
  };

  services.zigbee2mqtt = {
    enable = true;

    settings = {
      # Zigbee dongle — created by udev rule below.
      serial = {
        port = "/dev/zigbee";
        # Sonoff ZBDongle-P (CC2652P) — Z2M 2.x renamed "znp" → "zstack".
        adapter = "zstack";
      };

      # homeassistant MQTT discovery is enabled by default in the Z2M module.

      # Do not allow new devices to join by default.
      # Toggle from the Z2M web UI or via MQTT when pairing.
      permit_join = false;

      mqtt = {
        server = "mqtt://localhost:1883";
        user = "zigbee2mqtt";
        # Password injected via secret.yaml written in ExecStartPre.
        password = "!secret mqtt_password";
      };

      frontend = {
        enabled = true;
        port = 8099;
        host = "127.0.0.1";
      };
    };
  };

  # Write secret.yaml before Z2M starts so the MQTT password stays out of
  # /nix/store.  Runs as root ('+' prefix) so it can write before the
  # service user's StateDirectory permissions are applied.
  systemd.services.zigbee2mqtt.serviceConfig.ExecStartPre =
    let
      script = pkgs.writeShellScript "z2m-write-secret" ''
        set -euo pipefail
        password=$(cat ${config.lanbat.secrets.mosquitto-z2m-pass.path})
        printf 'mqtt_password: %s\n' "$password" \
          > /var/lib/zigbee2mqtt/secret.yaml
        chmod 0600 /var/lib/zigbee2mqtt/secret.yaml
        chown zigbee2mqtt /var/lib/zigbee2mqtt/secret.yaml
      '';
    in
    [ "+${script}" ];

  # Give Z2M access to the Zigbee USB dongle.
  users.groups.ha.gid = 993;
  users.users.zigbee2mqtt.extraGroups = [
    "dialout"
    "ha"
  ];

  # udev rule — creates /dev/zigbee symlink, group-owned by "ha".  The systemd
  # tag gives the dongle its device unit (dev-zigbee.device, through the
  # symlink), and SYSTEMD_WANTS starts Z2M whenever the dongle is plugged in.
  services.udev.extraRules = ''
    SUBSYSTEM=="tty", ATTRS{idVendor}=="${config.lanbat.deployment.zigbeeVendorId}", \
      ATTRS{idProduct}=="${config.lanbat.deployment.zigbeeProductId}", \
      SYMLINK+="zigbee", GROUP="ha", MODE="0660", \
      TAG+="systemd", ENV{SYSTEMD_WANTS}+="zigbee2mqtt.service"
  '';
}
