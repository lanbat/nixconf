# hosts/pi3/hardware.nix
#
# Raspberry Pi 3 (B or B+), booting from microSD with the stock NixOS aarch64
# SD image: U-Boot reading extlinux entries, mainline kernel from
# cache.nixos.org. hosts/pi5/hardware.nix is the Pi 5's (nixos-raspberrypi), so
# a Pi 3 host takes this one with `platform = "raspberry-pi-3"` in its deploy
# entry.
#
# Importing the SD image module gives the filesystems the image is flashed
# with (NIXOS_SD as /, FIRMWARE as /boot/firmware), the extlinux bootloader and
# the grow-on-first-boot of the root partition, so a card flashed from the
# official image switches to this configuration in place, the way the Pi 5 does
# (docs/pi3-satellite.md).
#
# 1 GB of RAM: compressed swap in RAM keeps a build or a nix evaluation from
# being killed. The journal is persistent but capped, so a crash leaves a log.
# A Pi 3 on a supply that sags crashes: the watchdog brings it back, and Nix
# flushes every store path to the card, so a crash mid-deploy damages none.
{
  config,
  lib,
  modulesPath,
  pkgs,
  ...
}:

{
  imports = [ (modulesPath + "/installer/sd-card/sd-image-aarch64.nix") ];

  nixpkgs.hostPlatform = "aarch64-linux";

  # Wi-Fi/Bluetooth firmware (brcmfmac43430/43455, BCM43430A1.hcd) and the VideoCore blobs.
  hardware.enableRedistributableFirmware = true;

  # The on-board Ethernet is a USB device (smsc95xx on the 3 B, lan78xx on the
  # 3 B+), so udev would name it after its MAC address. A fixed name lets the
  # deploy entry say `interface = "eth0"` before the Pi has ever booted.
  systemd.network.links."10-lan" = {
    matchConfig.Driver = "smsc95xx lan78xx";
    linkConfig.Name = "eth0";
  };

  zramSwap = {
    enable = true;
    memoryPercent = 100;
  };

  # Kept on the card, size- and age-capped, and flushed every 10 s so that what
  # was logged just before a crash or power loss survives it. A volatile journal
  # lost every panic message.
  services.journald.extraConfig = ''
    Storage=persistent
    SystemMaxUse=64M
    MaxRetentionSec=1week
    SyncIntervalSec=10s
  '';

  # A Pi 3 that crashed on a sagging supply stayed down until it was
  # power-cycled (2026-10-21). systemd pets the SoC's watchdog (bcm2835-wdt),
  # which resets the board when nothing has for 15 s, about the longest its
  # counter runs. A kernel panic stops the petting too.
  systemd.settings.Manager.RuntimeWatchdogSec = "15s";

  # The image's console loglevel of 7 floods the serial line and the journal.
  boot.consoleLogLevel = lib.mkForce 4;

  # The SD image puts a kernel console on ttyAMA0 (for QEMU's virt machine). On
  # the Pi 3 that PL011 UART is the Bluetooth chip's: kernel messages written to
  # it corrupt the HCI traffic, every command after the firmware patch times out
  # (Bluetooth: hci0: Opcode 0x0c24 failed: -110) and nothing can be scanned or
  # paired. The console goes to the mini UART (ttyS1) instead. This replaces the
  # whole list, so the parameters other modules contribute (hibernation, the
  # console log level, the LSM order) are repeated here.
  boot.kernelParams = lib.mkForce [
    "console=ttyS1,115200n8"
    "console=tty0"
    "nohibernate"
    "loglevel=${toString config.boot.consoleLogLevel}"
    "lsm=${lib.concatStringsSep "," config.security.lsm}"
  ];

  # The Bluetooth chip often fails its first initialisation after a warm reboot
  # ("Bluetooth: hci0: BCM: Reading local name failed (-110)") and then leaves
  # no controller; binding the driver again always works. This does that until
  # a controller shows up.
  systemd.services.bluetooth-uart-rebind = {
    description = "Re-initialise the Raspberry Pi 3 Bluetooth UART when it came up without a controller";
    after = [ "bluetooth.service" ];
    wantedBy = [ "bluetooth.target" ];
    serviceConfig.Type = "oneshot";
    script = ''
      driver=/sys/bus/serial/drivers/hci_uart_bcm
      for attempt in 1 2 3; do
        sleep 10
        if ${pkgs.bluez}/bin/bluetoothctl list | ${pkgs.gnugrep}/bin/grep -q '^Controller'; then
          exit 0
        fi
        echo serial0-0 > $driver/unbind || true
        sleep 2
        echo serial0-0 > $driver/bind || true
      done
    '';
  };

  # 1 GB of RAM: a nix build or copy on the Pi (nixos-rebuild --build-host) can
  # starve everything else until it hangs. Limit nix to one job on one core and
  # let the kernel stop it, not the satellite, when memory runs out.
  nix.settings = {
    max-jobs = 1;
    cores = 1;
    # A crash during a deploy left four files empty on the card, written but
    # not yet flushed, in paths Nix had already registered (found 2026-10-21).
    # A registered path is never copied again. Flushed before it is
    # registered, a path the crash interrupts is simply copied on the next try.
    fsync-store-paths = true;
  };
  systemd.services.nix-daemon.serviceConfig = {
    MemoryHigh = "400M";
    MemoryMax = "550M";
    OOMScoreAdjust = 500;
  };

  # WebRTC AEC in PipeWire is usable but costs CPU on the Pi 3; enable it only
  # when you need wake/stop words during playback.
  lanbat.voiceSatellite.echoCancellation.enable = lib.mkDefault false;

  # openWakeWord can't keep up on a Pi 3: hey_nabu took 109 % of a core and ran
  # at 0.9x realtime, against 7 % for a microWakeWord model (2026-10-06).
  warnings =
    let
      satellite = config.lanbat.voiceSatellite;
    in
    lib.optional
      (satellite.enable && satellite.backend == "lva" && lib.elem "hey_nabu" satellite.lva.wakeModels)
      "lanbat: hey_nabu is an openWakeWord model, which runs slower than realtime on a Pi 3 (109 % of a core); the satellite will fall behind its microphone. Use a microWakeWord model such as okay_nabu.";
}
