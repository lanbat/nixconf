# lib/roles/server.nix
#
# Server role: main compute host, reverse proxy, databases, containers.
# Hostname, static address and firewall baseline come from
# lib/roles/common.nix.
args@{
  config,
  pkgs,
  lib,
  ...
}:

{
  config = lib.mkMerge [
    (import ./common.nix args)
    {
      boot.loader = {
        systemd-boot.enable = true;
        efi.canTouchEfiVariables = true;
      };

      # A public resolver after the gateway, for when the gateway's DNS is down.
      networking.nameservers = [ "1.1.1.1" ];

      virtualisation.podman = {
        enable = true;
        dockerCompat = true;
        defaultNetwork.settings.dns_enabled = true;
        autoPrune.enable = true;
        autoPrune.dates = "weekly";
      };

      virtualisation.oci-containers.backend = "podman";

      # Keep enough compressed memory available for SSH and the identity path
      # when media workloads spike. zram is fast and is used first (priority
      # 100); the encrypted disk swap below takes over only once it fills.
      zramSwap = {
        enable = true;
        memoryPercent = 25;
        memoryMax = 8 * 1024 * 1024 * 1024;
        priority = 100;
      };

      # A 4 GiB swap LV (created by disko, hosts/server/disk.nix) as a cushion
      # beneath zram, so a memory spike does not global-OOM and wedge the host
      # (2026-11-01). It is encrypted with a fresh random key every boot: the
      # root is unencrypted, and Tang's private key and agenix secrets sit in
      # RAM, so swap must never reach plaintext disk. Lower priority than zram,
      # so the fast compressed swap is used first.
      swapDevices = [
        {
          device = "/dev/lanbat/swap";
          priority = 10;
          randomEncryption.enable = true;
        }
      ];

      # Auto-recover from a wedge instead of needing a physical power cycle.
      # systemd pets the hardware TCO watchdog, so the board resets if PID 1
      # stops petting for 30 s; a task stuck for five minutes (the reclaim
      # stall that wedged Caddy/Tang/sshd on 2026-11-01) panics the kernel, and
      # a panic reboots after 30 s.
      systemd.settings.Manager.RuntimeWatchdogSec = "30s";
      boot.kernel.sysctl = {
        "kernel.hung_task_timeout_secs" = 300;
        "kernel.hung_task_panic" = 1;
        "kernel.panic" = 30;
      };

      # Large container crashes must not fill the host root or monopolise I/O
      # while systemd-coredump compresses multi-gigabyte process images.
      systemd.coredump.extraConfig = ''
        Storage=external
        ProcessSizeMax=1G
        ExternalSizeMax=1G
        MaxUse=2G
        KeepFree=15G
      '';

      systemd.tmpfiles.rules = [
        "d /var/lib/homelab 0755 root root -"
      ];

      users.users.admin.extraGroups = [
        "media"
        "private"
      ];

      environment.systemPackages = [
        (pkgs.callPackage ../../pkgs/scripts { inherit (config.lanbat.deployment) domain; })
      ];
    }
  ];
}
