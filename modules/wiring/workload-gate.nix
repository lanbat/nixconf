# modules/wiring/workload-gate.nix
#
# Workload LUKS layer gating, generated from services with tier = "workload".
#
# For each such service:
#
#   1. /var/lib/<state> is a mode-0000 stub on the host root, bind-mounted
#      from /mnt/workload/<state> once the layer is unlocked. While locked the
#      stub is unreadable, so nothing can write service data to the host root.
#      The stubs use tmpfiles' ":" prefixes so a tmpfiles run on a live system
#      never resets the mode or owner of the mounted directory.
#
#   2. Its units move from multi-user.target to workload-online.target and
#      bind to it:
#        WantedBy=workload-online.target   (not multi-user.target)
#        After=workload-online.target workload-init.service
#        BindsTo=workload-online.target    (stop when the layer goes away)
#
#   3. workload-init creates the /mnt/workload directories (state and
#      workloadDirs) before the bind mounts activate.
#
# workload-online.target is never started at boot; unlock-workload starts it.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  gated = lib.filterAttrs (_: svc: svc.tier == "workload") config.lanbat.services;

  stateDirs = lib.unique (lib.concatLists (lib.mapAttrsToList (_: svc: svc.state) gated));
  gatedUnits = lib.unique (lib.concatLists (lib.mapAttrsToList (_: svc: svc.units) gated));
  # From every service: an always-on one may keep a directory on the layer too
  # (voice-id's voiceprints), and must cope while it is locked.
  workloadDirs = lib.foldl' (acc: svc: acc // svc.workloadDirs) { } (
    lib.attrValues config.lanbat.services
  );

  # /var/lib/nextcloud → var-lib-nextcloud.mount
  mountUnit = path: "${lib.replaceStrings [ "/" ] [ "-" ] (lib.removePrefix "/" path)}.mount";
  bindMountUnits = map (d: mountUnit "/var/lib/${d}") stateDirs;

  adminScript =
    name: body:
    pkgs.writeShellScriptBin name ''
      set -euo pipefail
      ${body}
    '';
in
{
  options.lanbat.layers = {
    workloadDevice = lib.mkOption {
      type = lib.types.str;
      example = "/dev/lanbat/workload";
      description = "Block device holding the workload LUKS volume. Set by the host's disk layout.";
    };

    workloadFileSystems = lib.mkOption {
      type = lib.types.attrsOf lib.types.anything;
      internal = true;
      readOnly = true;
      description = "The generated workload mounts. Tests pass them to virtualisation.fileSystems.";
    };
  };

  config = {
    lanbat.wiring.workloadGate = true;

    systemd.tmpfiles.rules = [
      "d /mnt/workload :0000 :root :root -"
    ]
    ++ map (d: "d /var/lib/${d} :0000 :root :root -") stateDirs;

    fileSystems = config.lanbat.layers.workloadFileSystems;

    # noauto: not part of local-fs.target, so nothing mounts at boot.
    lanbat.layers.workloadFileSystems = {
      "/mnt/workload" = {
        device = "/dev/mapper/workload-luks";
        fsType = "ext4";
        options = [
          "noauto"
          "noatime"
          "x-systemd.idle-timeout=0"
        ];
      };
    }
    // lib.genAttrs (map (d: "/var/lib/${d}") stateDirs) (path: {
      device = "/mnt/workload/${lib.removePrefix "/var/lib/" path}";
      fsType = "none";
      # nofail stops systemd ordering the mount before local-fs.target. With that
      # ordering, a transaction that starts local-fs.target and a new bind mount
      # together (a switch that adds a workload service while the layer is
      # unlocked) is cyclic: the mount is after workload-init.service, which is
      # after sysinit.target, which is after local-fs.target. nofail doesn't
      # make the mount optional for workload-online.target, which still
      # requires it, so a failed bind mount keeps services off the empty stubs.
      options = [
        "bind"
        "noauto"
        "nofail"
        "x-systemd.requires=mnt-workload.mount"
        "x-systemd.after=mnt-workload.mount"
      ];
    });

    systemd.targets.workload-online = {
      description = "Workload LUKS layer mounted and all service data available";
      requires = [ "mnt-workload.mount" ] ++ bindMountUnits;
      after = [ "mnt-workload.mount" ] ++ bindMountUnits;
      # No wantedBy: unlock-workload starts it.
    };

    systemd.services =
      lib.genAttrs gatedUnits (_: {
        wantedBy = lib.mkForce [ "workload-online.target" ];
        after = lib.mkAfter [
          "workload-online.target"
          "workload-init.service"
        ];
        bindsTo = [ "workload-online.target" ];
      })
      // {
        workload-init = {
          description = "Initialize workload directory structure";
          after = [ "mnt-workload.mount" ];
          requires = [ "mnt-workload.mount" ];
          before = bindMountUnits;
          wantedBy = [ "workload-online.target" ];
          partOf = [ "workload-online.target" ];
          serviceConfig = {
            Type = "oneshot";
            RemainAfterExit = true;
            ExecStart = pkgs.writeShellScript "workload-init" ''
              set -euo pipefail
              W=/mnt/workload
              ${lib.concatMapStringsSep "\n" (d: ''mkdir -p "$W/${d}"'') stateDirs}
              ${lib.concatStringsSep "\n" (
                lib.mapAttrsToList (
                  path: dir: ''install -d -m ${dir.mode} -o ${dir.user} -g ${dir.group} "$W/${path}"''
                ) workloadDirs
              )}
            '';
          };
        };
      };

    environment.systemPackages = [
      # unlock-workload: open the workload LUKS volume, mount it, start services.
      (adminScript "unlock-workload" ''
        echo "=== unlock-workload: opening workload LUKS layer ==="
        echo
        if [ -e /dev/mapper/workload-luks ]; then
          echo "INFO: /dev/mapper/workload-luks already exists, skipping luksOpen."
        else
          cryptsetup luksOpen ${config.lanbat.layers.workloadDevice} workload-luks
        fi
        echo "Mounting /mnt/workload and activating workload-online.target..."
        systemctl start workload-online.target
        echo
        echo "Workload is online."
      '')

      # lock-workload: stop all gated services, unmount, close LUKS.
      (adminScript "lock-workload" ''
        echo "=== lock-workload: stopping workload services and locking layer ==="
        echo
        echo "This stops every workload-gated service."
        read -r -p "Continue? [y/N] " confirm
        [[ "$confirm" == [yY] ]] || { echo "Aborted."; exit 1; }
        echo "Stopping workload-online.target (propagates to all bound services)..."
        systemctl stop workload-online.target 2>/dev/null || true
        echo "Waiting for services to stop..."
        sleep 5
        for mount in ${lib.concatStringsSep " " bindMountUnits}; do
          systemctl stop "$mount" 2>/dev/null || true
        done
        if mountpoint -q /mnt/workload; then
          umount /mnt/workload
        fi
        if [ -e /dev/mapper/workload-luks ]; then
          cryptsetup luksClose workload-luks
          echo "Workload LUKS closed."
        else
          echo "INFO: /dev/mapper/workload-luks not found, already closed."
        fi
      '')
    ];
  };
}
