# tests/workload-gate.nix
#
# VM test of the workload layer: a workload-tier service must stay down while
# the layer is locked, start after unlock-workload, keep its directory
# permissions across a tmpfiles run, and stop again after lock-workload.
#
# Run with: nix build .#checks.x86_64-linux.workload-gate
{ pkgs }:

pkgs.testers.runNixOSTest {
  name = "workload-gate";

  nodes.machine =
    { config, pkgs, ... }:
    {
      imports = [
        ../modules/core/services.nix
        ../modules/wiring/checks.nix
        ../modules/wiring/workload-gate.nix
      ];

      # Test VMs replace fileSystems with virtualisation.fileSystems.
      virtualisation.fileSystems = config.lanbat.layers.workloadFileSystems;

      virtualisation.emptyDiskImages = [ 64 ];
      lanbat.layers.workloadDevice = "/dev/vdb";

      lanbat.services.demo = {
        tier = "workload";
        state = [ "demo" ];
        units = [ "demo" ];
        workloadDirs."demo".user = "demo";
      };

      # An always-on service that keeps a directory on the layer, as voice-id
      # does: up while locked, restarted once the layer is open so its sandbox
      # sees the directory.
      lanbat.services.keeper.workloadDirs."keeper".user = "keeper";

      users.users.demo = {
        isSystemUser = true;
        group = "demo";
      };
      users.groups.demo = { };
      users.users.keeper = {
        isSystemUser = true;
        group = "keeper";
      };
      users.groups.keeper = { };

      systemd.services.keeper = {
        wantedBy = [ "multi-user.target" ];
        serviceConfig = {
          User = "keeper";
          ProtectSystem = "strict";
          ReadWritePaths = [ "-/mnt/workload/keeper" ];
        };
        script = "exec sleep infinity";
      };

      systemd.services.demo = {
        wantedBy = [ "multi-user.target" ];
        serviceConfig.User = "demo";
        script = ''
          echo ok > /var/lib/demo/marker
          exec sleep infinity
        '';
      };

      environment.systemPackages = [ pkgs.cryptsetup ];
    };

  testScript = ''
    machine.wait_for_unit("multi-user.target")

    with subtest("locked: service down, stub closed"):
        machine.fail("systemctl is-active demo.service")
        machine.succeed("test \"$(stat -c %a /var/lib/demo)\" = 0")

    with subtest("create the workload volume"):
        machine.succeed(
            "printf secret | cryptsetup luksFormat --batch-mode --pbkdf pbkdf2 --pbkdf-force-iterations 1000 /dev/vdb",
            "printf secret | cryptsetup luksOpen /dev/vdb workload",
            "mkfs.ext4 -q /dev/mapper/workload",
            "cryptsetup luksClose workload",
        )

    with subtest("unlock-workload starts the service on the encrypted layer"):
        machine.succeed("printf secret | unlock-workload")
        machine.wait_for_unit("demo.service")
        machine.wait_for_file("/mnt/workload/demo/marker")
        machine.succeed("mountpoint -q /var/lib/demo")

    with subtest("an always-on service keeps running and sees its directory"):
        machine.succeed("systemctl is-active keeper.service")
        machine.succeed("systemctl restart keeper.service")
        machine.succeed("nsenter -t $(systemctl show -p MainPID --value keeper.service) -m test -d /mnt/workload/keeper")

    with subtest("a tmpfiles run keeps the live directory open"):
        machine.succeed("systemctl restart systemd-tmpfiles-resetup.service")
        machine.succeed("test \"$(stat -c %U:%a /var/lib/demo)\" = demo:750")
        machine.succeed("systemctl is-active demo.service")

    with subtest("lock-workload stops the service and closes the volume"):
        machine.succeed("printf 'y\\n' | lock-workload")
        machine.fail("systemctl is-active demo.service")
        machine.fail("test -e /dev/mapper/workload")
        machine.succeed("systemctl is-active keeper.service")
        machine.succeed("test \"$(stat -c %a /var/lib/demo)\" = 0")
  '';
}
