# modules/server/android-devices.nix
#
# Declarative provisioning for Android TV boxes over ADB.  Enabled through the
# lanbat-android plugin; configures nothing when no devices are declared.
#
# What it does
# ------------
# Each device in androidDevices becomes a manifest (apps pinned by
# pkgs/android-provision/apks.lock.json, CA certificates, settings, an
# Obtainium URL list) plus a oneshot unit that converges the box to it.
# Nothing runs on a timer: a box is provisioned when asked, never while
# someone is watching it.
#
# Capture
# -------
# Each device also gets an android-capture-<name> oneshot unit that takes a
# read-only snapshot (apps, settings, home screen) into its state directory,
# for comparing a box before and after a reset. It never changes the device.
#
# ADB identity
# ------------
# adb reads its client key from $HOME/.android/adbkey, so the units run with
# HOME pointing at the state directory and adb creates the key there once.
# The first connection to a box raises an on-screen "Allow USB debugging?"
# dialog, accepted once with "always allow" -- a key regenerated per
# activation would re-prompt on every run, in front of the TV.
#
# What it deliberately does not do
# --------------------------------
# It never uninstalls anything, and it never factory-resets a box.  Removing
# an app from the configuration removes nothing from the device.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  inherit (lib) mkOption types;

  devices = lib.filterAttrs (_: d: d.enable) config.androidDevices;

  # Home Assistant's Android TV integration uses the key the boxes already
  # trust (the provisioner's), so they ask for no second authorisation. It
  # gets a copy it owns; the provisioner's own stays root's.
  adbkeyForHomeAssistant = lib.optionalAttrs (config.services.home-assistant.enable or false) {
    android-adbkey-for-home-assistant = {
      description = "Give Home Assistant the provisioning ADB key";
      wantedBy = [ "multi-user.target" ];
      before = [
        "home-assistant.service"
        "home-assistant-post-setup.service"
      ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        src=/var/lib/android-provision/.android
        dst=${config.services.home-assistant.configDir}/.android
        [ -s "$src/adbkey" ] || { echo "no provisioning ADB key yet; run android-provision first"; exit 0; }
        install -d -o hass -g hass -m 0700 "$dst"
        install -o hass -g hass -m 0600 "$src/adbkey" "$dst/adbkey"
        [ -s "$src/adbkey.pub" ] && install -o hass -g hass -m 0644 "$src/adbkey.pub" "$dst/adbkey.pub"
      '';
    };
  };

  provisioner = pkgs.callPackage ../../pkgs/android-provision { };

  lock = lib.importJSON ../../pkgs/android-provision/apks.lock.json;

  # One fetchurl per locked APK variant actually needed by a device.
  fetchApk =
    entry: abi:
    let
      variant = entry.variants.${abi} or entry.variants.universal;
    in
    pkgs.fetchurl {
      url = variant.url;
      hash = variant.sha256;
      name = "${entry.packageId}-${toString entry.versionCode}.apk";
    };

  apkEntries =
    device:
    let
      keys = device.packages ++ (map (g: g.repo) device.github);
    in
    map (
      key:
      let
        entry = lock.${key};
      in
      {
        inherit (entry)
          packageId
          versionCode
          versionName
          minSdk
          source
          ;
        path = "${fetchApk entry device.abi}";
      }
    ) keys;

  obtainiumFile =
    device:
    pkgs.writeText "obtainium-urls.txt" (lib.concatMapStrings (o: o.url + "\n") device.obtainium);

  manifestFor =
    name: device:
    pkgs.writeText "android-manifest-${name}.json" (
      builtins.toJSON {
        device = name;
        inherit (device)
          host
          port
          abi
          allowDowngrade
          ;
        homeActivity = device.homeActivity;
        apks = apkEntries device;
        caCerts = map (cert: {
          name = baseNameOf (toString cert);
          sha256 = builtins.hashFile "sha256" cert;
          path = "${cert}";
        }) device.caCerts;
        settings = device.settings;
        obtainium =
          if device.obtainium == [ ] then
            null
          else
            {
              path = "${obtainiumFile device}";
              sha256 = builtins.hashString "sha256" (lib.concatMapStrings (o: o.url + "\n") device.obtainium);
            };
        deviceOwner = {
          inherit (device.deviceOwner) enable component;
        };
        # The android-presence app is a generic room sensor; this gives it the
        # deployment's room, router socket and every person's beacon UUID.
        presence =
          if device.presence == null || device.room == null then
            null
          else {
            room = device.room;
            inherit (device.presence) routerUrl reportSeconds windowSeconds;
            beacons = lib.mapAttrs' (
              key: person: lib.nameValuePair (lib.toLower person.bleBeacon) key
            ) (
              lib.filterAttrs (_: person: (person.bleBeacon or null) != null) (
                config.lanbat.deployment.people or { }
              )
            );
          };
      }
    );

  unitFor =
    name: device: plan:
    let
      suffix = if plan then "-plan" else "";
      verb = if plan then "plan" else "provision";
    in
    lib.nameValuePair "android-provision-${name}${suffix}" {
      description = "${if plan then "Plan" else "Apply"} provisioning for Android device ${name}";
      serviceConfig = {
        Type = "oneshot";
        User = "root";
        StateDirectory = "android-provision";
        StateDirectoryMode = "0700";
        # adb keeps its client key in $HOME/.android/adbkey.
        Environment = "HOME=/var/lib/android-provision";
        TimeoutStartSec = "30min";
      };
      script = ''
        exec ${lib.getExe provisioner} ${verb} --manifest ${manifestFor name device}
      '';
    };

  # Read-only snapshot of the box (apps, settings, home screen), for comparing
  # before and after a reset. Snapshots hold device identifiers, so they stay
  # in the state directory and never enter the repository.
  captureUnitFor =
    name: device:
    lib.nameValuePair "android-capture-${name}" {
      description = "Snapshot Android device ${name}";
      serviceConfig = {
        Type = "oneshot";
        User = "root";
        StateDirectory = "android-provision";
        StateDirectoryMode = "0700";
        Environment = "HOME=/var/lib/android-provision";
        UMask = "0077";
      };
      script = ''
        exec ${lib.getExe provisioner} capture --manifest ${manifestFor name device} \
          --out-dir /var/lib/android-provision/${name}/snapshots
      '';
    };

  deviceModule = {
    options = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Whether to provision this device.";
      };

      host = mkOption {
        type = types.str;
        example = "192.0.2.50";
        description = "Device address. ADB over TCP must already be enabled on it.";
      };

      port = mkOption {
        type = types.port;
        default = 5555;
        description = "ADB over TCP port.";
      };

      room = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "Living Room";
        description = ''
          Home Assistant area the box is in. Home Assistant controls it over ADB
          (its Android TV integration, with the provisioning key), and voice
          commands such as "open SmartTube" or "pause the TV" act on the box in
          the room of the satellite that heard them.
        '';
      };

      presence = mkOption {
        type = types.nullOr (
          types.submodule {
            options = {
              routerUrl = mkOption {
                type = types.str;
                example = "ws://192.168.1.10:8770/v1/body";
                description = "The assistant router's room-sensor socket.";
              };
              reportSeconds = mkOption {
                type = types.ints.positive;
                default = 2;
                description = "How often the app reports sightings.";
              };
              windowSeconds = mkOption {
                type = types.ints.positive;
                default = 20;
                description = "The RSSI median window.";
              };
            };
          }
        );
        default = null;
        description = ''
          Make this box a Bluetooth room sensor for the assistant router: it
          installs none of this itself, so add the android-presence app to
          `github`/`packages`, and give the box a `room`. Every person with a
          `bleBeacon` in `lanbat.deployment.people` is scanned for. The app
          reports the median RSSI to the router, which places the person in
          `room` like a Pi room-presence scanner does.
        '';
      };

      apps = mkOption {
        type = types.attrsOf types.str;
        default = { };
        example = {
          SmartTube = "com.liskovsoft.smarttubetv.beta";
          Netflix = "com.netflix.ninja";
        };
        description = ''
          Apps by the name you say ("okay nabu, open SmartTube") and their
          Android package. Home Assistant lists them as the box's sources.
        '';
      };

      abi = mkOption {
        type = types.str;
        default = "arm64-v8a";
        description = ''
          Device ABI, used to pick an APK variant.  Apps such as VLC publish one
          APK per ABI, so the package identifier alone is ambiguous.
        '';
      };

      packages = mkOption {
        type = types.listOf types.str;
        default = [ ];
        example = [ "de.badaix.snapcast" ];
        description = "F-Droid package identifiers, pinned by apks.lock.json.";
      };

      github = mkOption {
        type = types.listOf (
          types.submodule {
            options = {
              repo = mkOption {
                type = types.str;
                example = "theothernt/AerialViews";
                description = "GitHub owner/repo.";
              };
              asset = mkOption {
                type = types.str;
                default = "*.apk";
                description = "Glob matching exactly one release asset.";
              };
            };
          }
        );
        default = [ ];
        description = "GitHub releases, pinned by apks.lock.json.";
      };

      obtainium = mkOption {
        type = types.listOf (
          types.submodule {
            options.url = mkOption {
              type = types.str;
              description = "App source URL for Obtainium to track.";
            };
          }
        );
        default = [ ];
        description = ''
          Apps handed to Obtainium, which owns their updates.  Nothing here is
          installed by the provisioner.
        '';
      };

      caCerts = mkOption {
        type = types.listOf types.path;
        # The profile's CA when the module runs inside lanbat; this repository's
        # own certificate when it is imported on its own.
        default = [
          (config.lanbat.deployment.secrets.caCertificate or ../../secrets/caddy-ca-root.crt)
        ];
        defaultText = lib.literalExpression "[ config.lanbat.deployment.secrets.caCertificate ]";
        description = ''
          CA certificates to install into the user trust store.  Defaults to the
          internal root Caddy issues from.  Setting this replaces the default
          rather than adding to it.

          Since Android 7 apps ignore user CAs unless they opt in, so this fixes
          the browser and not Kodi, Jellyfin or YouTube.
        '';
      };

      settings = mkOption {
        type = types.attrsOf (types.attrsOf (types.either types.str types.int));
        default = { };
        example = {
          global.screen_off_timeout = 600000;
        };
        description = "settings put values, by namespace (global, secure, system).";
      };

      allowDowngrade = mkOption {
        type = types.bool;
        default = false;
        description = "Replace an installed app that is newer than the lockfile.";
      };

      homeActivity = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "com.nendo.argosy/.MainActivity";
        description = ''
          Activity to make the default home screen, as package/activity.
          Set after the apps are installed; a box that refuses is reported
          as failed.
        '';
      };

      deviceOwner = {
        enable = mkOption {
          type = types.bool;
          default = false;
          description = ''
            Set a Device Owner.  This can only succeed on a box with no
            configured accounts, in practice right after a factory reset, which
            the provisioner will never perform for you.
          '';
        };
        component = mkOption {
          type = types.nullOr types.str;
          default = null;
          example = "com.example.dpc/.AdminReceiver";
          description = "DPC admin receiver component.";
        };
      };
    };
  };
in
{
  options.androidDevices = mkOption {
    type = types.attrsOf (types.submodule deviceModule);
    default = { };
    description = "Android TV boxes to provision over ADB.";
  };

  config = lib.mkIf (devices != { }) {

    assertions =
      let
        # Group device names by "host:port" so a collision names every
        # device sharing it, not just that a collision exists.
        endpointGroups = lib.foldlAttrs (
          acc: name: d:
          let
            endpoint = "${d.host}:${toString d.port}";
          in
          acc // { ${endpoint} = (acc.${endpoint} or [ ]) ++ [ name ]; }
        ) { } devices;
      in
      lib.concatLists (
        lib.mapAttrsToList (
          endpoint: names:
          lib.optional (lib.length names > 1) {
            assertion = false;
            message = "androidDevices: ${lib.concatStringsSep " and " names} share the same host:port ${endpoint}.";
          }
        ) endpointGroups
      )
      ++ lib.mapAttrsToList (name: d: {
        assertion = !d.deviceOwner.enable || d.deviceOwner.component != null;
        message = "androidDevices.${name}: deviceOwner.enable needs deviceOwner.component.";
      }) devices
      ++ lib.mapAttrsToList (name: d: {
        assertion = d.presence == null || d.room != null;
        message = "androidDevices.${name}: presence needs a room (set `room`).";
      }) devices
      ++ lib.mapAttrsToList (
        name: d:
        let
          parts = if d.homeActivity == null then [ ] else lib.splitString "/" d.homeActivity;
        in
        {
          assertion = d.homeActivity == null || (lib.length parts == 2 && lib.all (p: p != "") parts);
          message = "androidDevices.${name}: homeActivity \"${toString d.homeActivity}\" must have the form package/activity.";
        }
      ) devices
      ++ lib.concatLists (
        lib.mapAttrsToList (
          name: d:
          map (key: {
            assertion = lock ? ${key};
            message =
              "androidDevices.${name}: ${key} is not in pkgs/android-provision/apks.lock.json. "
              + "Run: nix run .#android-update";
          }) (d.packages ++ (map (g: g.repo) d.github))
        ) devices
      )
      # A key present in the lockfile might still lack a variant for this
      # device's abi (and no universal fallback). Checked only for keys the
      # previous assertion has already confirmed are in the lockfile, so
      # this never dereferences a missing lock entry.
      ++ lib.concatLists (
        lib.mapAttrsToList (
          name: d:
          map (
            key:
            let
              entry = lock.${key};
            in
            {
              assertion = entry.variants ? ${d.abi} || entry.variants ? universal;
              message =
                "androidDevices.${name}: ${entry.packageId} has no APK variant for abi \"${d.abi}\" "
                + "(available: ${lib.concatStringsSep ", " (lib.attrNames entry.variants)}).";
            }
          ) (lib.filter (key: lock ? ${key}) (d.packages ++ (map (g: g.repo) d.github)))
        ) devices
      )
      # settings is attrsOf (attrsOf ...) with no namespace check in the
      # type, so a typo like `globl` would otherwise only be caught at
      # provision time (exit 4) instead of at eval time.
      ++ lib.concatLists (
        lib.mapAttrsToList (
          name: d:
          map (ns: {
            assertion = builtins.elem ns [
              "global"
              "secure"
              "system"
            ];
            message =
              "androidDevices.${name}: unknown settings namespace \"${ns}\" "
              + "(expected global, secure or system).";
          }) (lib.attrNames d.settings)
        ) devices
      );

    environment.systemPackages = [ provisioner ];

    systemd.services =
      adbkeyForHomeAssistant
      // lib.listToAttrs (
        lib.concatLists (
          lib.mapAttrsToList (name: d: [
            (unitFor name d false)
            (unitFor name d true)
            (captureUnitFor name d)
          ]) devices
        )
      );
  };
}
