# modules/core/base.nix
#
# Settings that are identical across both machines: locale, timezone,
# Nix daemon options, basic packages, and a few quality-of-life tweaks.
{
  config,
  options,
  pkgs,
  lib,
  ...
}:

{
  # ---------------------------------------------------------------------------
  # Locale / time
  # ---------------------------------------------------------------------------
  time.timeZone = config.lanbat.deployment.timezone;
  i18n.defaultLocale = "en_US.UTF-8";

  # ---------------------------------------------------------------------------
  # Nix daemon
  # ---------------------------------------------------------------------------
  nix = {
    settings = {
      experimental-features = [
        "nix-command"
        "flakes"
      ];
      # Deduplicate store paths on builds.
      auto-optimise-store = true;
      # Garbage collection, including the during-build min-free/max-free valve,
      # is configured in modules/core/gc.nix under lanbat.gc.
      # Users the Nix daemon trusts, e.g. to accept store paths copied by deploy-rs.
      trusted-users = [
        "root"
        "@wheel"
      ];
    };
  };

  # ---------------------------------------------------------------------------
  # Base packages available on both machines
  # ---------------------------------------------------------------------------
  environment.systemPackages = with pkgs; [
    # Diagnostics
    htop
    iotop
    ncdu
    lsof
    strace
    tcpdump
    nmap
    iproute2
    ethtool

    # File tools
    rsync
    git
    jq
    yq-go
    vim
    less
    tree
    file
    unzip
    zip
    gptfdisk
    parted

    # Crypto / security
    age
    openssl

    # Network
    curl
    wget
    dnsutils
    iputils

    # System
    smartmontools
    hdparm
    util-linux
    lvm2
    cryptsetup
  ];

  # ---------------------------------------------------------------------------
  # SSH hardening (openssh enabled per-host)
  # ---------------------------------------------------------------------------
  programs.ssh.startAgent = false;

  # ---------------------------------------------------------------------------
  # Basic audit trail
  # ---------------------------------------------------------------------------
  # NixOS 26.11 moved journald's options under settings.Journal, but
  # nixos-raspberrypi still pins a 26.05 nixpkgs for the Pi 5.
  services.journald =
    if options.services.journald ? settings then
      {
        settings.Journal = {
          SystemMaxUse = "2G";
          MaxRetentionSec = "90day";
        };
      }
    else
      {
        extraConfig = ''
          SystemMaxUse=2G
          MaxRetentionSec=90day
        '';
      };
}
