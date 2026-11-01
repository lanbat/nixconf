# hosts/server/disk.nix
#
# Disk layout of the server, applied by disko during installation
# (nixos-anywhere). It ERASES lanbat.serverDisk.
#
#   ESP      1 GiB   vfat  /boot
#   LVM PV   rest    volume group "lanbat":
#     root      150 GiB    ext4  /              host layer, always available
#     control   1 GiB      LUKS2 → ext4         control layer, /mnt/control
#     workload  80% free   LUKS2 → ext4         workload layer, /mnt/workload
#     swap      4 GiB      dm-crypt (random)    OOM cushion, encrypted swap; the
#                                               swap device is declared in
#                                               lib/roles/server.nix, not here
#     (free)    ~20%       unallocated headroom
#
# Why LVM with free space: the host root holds the Nix store, container
# images and the state of every always-on service, and a fixed-size root
# partition fills up. When root or workload runs low, grow it online from the
# free space (see docs/storage-layout.md):
#
#   lvextend -r -L +50G lanbat/root
#   lvextend -L +200G lanbat/workload && cryptsetup resize workload && resize2fs /dev/mapper/workload
#
# The LUKS volumes are formatted with the passphrases in /tmp/control.key and
# /tmp/workload.key, which nixos-anywhere uploads with --disk-encryption-keys.
# They are never unlocked at boot (initrdUnlock = false) and have no
# mountpoint here: modules/server/control-layer.nix and
# modules/wiring/workload-gate.nix mount them after a manual unlock.
{ config, ... }:

let
  lockedVolume = name: {
    type = "luks";
    # "control" is a reserved device-mapper name (/dev/mapper/control).
    name = "${name}-luks";
    initrdUnlock = false;
    passwordFile = "/tmp/${name}.key";
    settings.allowDiscards = true;
    content = {
      type = "filesystem";
      format = "ext4";
    };
  };
in
{
  disko.devices = {
    disk.system = {
      type = "disk";
      device = config.lanbat.hosts.${config.lanbat.hostKey}.disks.system;
      content = {
        type = "gpt";
        partitions = {
          ESP = {
            size = "1G";
            type = "EF00";
            content = {
              type = "filesystem";
              format = "vfat";
              mountpoint = "/boot";
              mountOptions = [ "umask=0077" ];
            };
          };
          lvm = {
            size = "100%";
            content = {
              type = "lvm_pv";
              vg = "lanbat";
            };
          };
        };
      };
    };

    lvm_vg.lanbat = {
      type = "lvm_vg";
      lvs = {
        root = {
          size = "150G";
          content = {
            type = "filesystem";
            format = "ext4";
            mountpoint = "/";
            mountOptions = [ "noatime" ];
          };
        };
        control = {
          size = "1G";
          content = lockedVolume "control";
        };
        # Created last, so the percentage applies to what root and control left.
        workload = {
          size = "80%FREE";
          priority = 2000;
          content = lockedVolume "workload";
        };
        # A bare LV: lib/roles/server.nix turns it into encrypted swap (dm-crypt
        # with a random key each boot), so disko must not format it. Fixed size,
        # so it never affects workload's 80%FREE above.
        swap = {
          size = "4G";
        };
      };
    };
  };

  lanbat.layers = {
    controlDevice = "/dev/lanbat/control";
    workloadDevice = "/dev/lanbat/workload";
  };
}
