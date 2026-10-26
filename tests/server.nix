# tests/server.nix
#
# VM test of the server role with deploy.example settings and test-only secrets.
{
  pkgs,
  agenix,
  disko,
  inputs,
}:

let
  inherit (pkgs) lib;

  # The services come from the built-in registry, chosen the way lib/mkHost.nix
  # chooses them for the example profile's server: the names its deploy entry
  # lists in hosts.server.services, or every registered service when it lists
  # none. Only the services plugin is resolved; the example server's other
  # plugins are not part of this test.
  exampleServer =
    (import ../deployments/example/deploy.nix {
      inputs.self.lanbatPlugins = { };
    }).hosts.server;
  roleModules = (import ../lib/roles.nix { inherit lib; }).getRoleModules exampleServer.role;
  serviceModules = (import ../lib/plugins.nix { inherit lib; }).resolvePlugins exampleServer.role [
    (import ../plugins/services)
  ] (exampleServer.services or [ ]);

  # A throwaway internal CA, so Caddy signs with a root key that matches the
  # certificate it serves, as on a real host.
  testCa = pkgs.runCommand "lanbat-test-ca" { nativeBuildInputs = [ pkgs.openssl ]; } ''
    mkdir $out
    openssl ecparam -name prime256v1 -genkey -noout -out $out/root.key
    openssl req -x509 -new -key $out/root.key -sha256 -days 3650 \
      -subj "/CN=Lanbat Test Root CA" \
      -addext "basicConstraints=critical,CA:TRUE" \
      -addext "keyUsage=critical,keyCertSign,cRLSign" \
      -out $out/root.crt
  '';
in
pkgs.testers.runNixOSTest {
  name = "server";

  node.pkgsReadOnly = false;

  # services/zigbee2mqtt.nix pins its package from inputs.nixpkgs-z2m, so the
  # VM runs the same Zigbee2MQTT as the hosts.
  node.specialArgs = { inherit inputs; };

  nodes.server =
    { config, lib, ... }:
    {
      imports = [
        agenix.nixosModules.default
        disko.nixosModules.disko
        ../modules/core
        ./lib/example-host-context.nix
        ./lib/test-secrets.nix
      ]
      # The role's modules as lib/mkHost.nix imports them, so the test boots
      # the same wiring (Caddy vhosts, NFS, on-demand, workload gating) as
      # the host.
      ++ roleModules
      ++ serviceModules;

      nixpkgs.config.allowUnfree = true;

      virtualisation = {
        memorySize = 8192;
        cores = 4;
        diskSize = 8192;
        emptyDiskImages = [
          64
          2048
        ];
        fileSystems = config.lanbat.layers.controlFileSystems // config.lanbat.layers.workloadFileSystems;
      };

      # The example server runs every service, Frigate included, without the
      # example's camera module; Frigate refuses that unless told.
      lanbat.services.frigate.settings.allowNoCameras = true;

      lanbat.layers = {
        controlDevice = lib.mkForce "/dev/vdb";
        workloadDevice = lib.mkForce "/dev/vdc";
      };

      systemd.services = lib.mapAttrs' (
        name: _: lib.nameValuePair "podman-${name}" { wantedBy = lib.mkForce [ ]; }
      ) config.virtualisation.oci-containers.containers;

      # The whole set: example-host-context.nix sets deployment at mkDefault.
      lanbat.deployment.secrets = {
        provider = "none";
        root = ../secrets;
        caCertificate = "${testCa}/root.crt";
      };
      lanbat.testSecretFiles.caddy-ca-root-key = "${testCa}/root.key";

      lanbat.testSecrets = {
        authentik-env = ''
          AUTHENTIK_POSTGRESQL__PASSWORD=test-db-password
          AUTHENTIK_SECRET_KEY=test-secret-key-0123456789abcdef0123456789abcdef
        '';
        authentik-oidc-secrets = ''
          AUTHENTIK_GRAFANA_CLIENT_SECRET=test
          AUTHENTIK_NEXTCLOUD_CLIENT_SECRET=test
          AUTHENTIK_IMMICH_CLIENT_SECRET=test
          AUTHENTIK_HA_CLIENT_SECRET=test
          AUTHENTIK_JELLYFIN_CLIENT_SECRET=test
          AUTHENTIK_VAULTWARDEN_CLIENT_SECRET=test
        '';
        nextcloud-admin-pass = "test-admin-password";
        nextcloud-oidc-env = ''
          NEXTCLOUD_OIDC_CLIENT_ID=nextcloud
          NEXTCLOUD_OIDC_CLIENT_SECRET=test
        '';
        immich-db-password = "POSTGRES_PASSWORD=test-db-password\n";
        immich-oidc-env = ''
          IMMICH_OAUTH_CLIENT_ID=immich
          IMMICH_OAUTH_CLIENT_SECRET=test
        '';
        bitmagnet-db-pass = "POSTGRES_PASSWORD=test-db-password\n";
        frigate-rtsp-env = ''
          FRIGATE_RTSP_USER=test
          FRIGATE_RTSP_PASSWORD=test
        '';
        rclone-frigate-config = "[remote]\ntype = local\n";
        ha-voice-token = "test-voice-token";
        ha-voice-refresh-token = "VOICE_TOKEN_ID=test\nVOICE_TOKEN_JWT_KEY=test\nVOICE_TOKEN_CREATED=0\n";
        mosquitto-ha-pass = "test-mqtt-password";
        mosquitto-frigate-pass = "test-mqtt-password";
        mosquitto-z2m-pass = "test-mqtt-password";
        influxdb-admin-password = "test-influx-password";
        influxdb-admin-token = "test-influx-token-0123456789";
        grafana-env = ''
          GF_SECURITY_SECRET_KEY=test-grafana-secret-key
          GF_SECURITY_ADMIN_PASSWORD=test-admin-password
          GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET=test
          INFLUXDB_TOKEN=test-influx-token-0123456789
        '';
        vaultwarden-env = ''
          ADMIN_TOKEN=test
          SSO_CLIENT_SECRET=test
        '';
        # A token of its own: InfluxDB provisioning sets the Telegraf auth to
        # this value, and an auth sharing the operator token's value would
        # take the operator token over.
        telegraf-token = "TELEGRAF_INFLUXDB_TOKEN=test-telegraf-token-0123456789\n";
      };
    };

  testScript = ''
    import time

    server.start()

    for _ in range(30):
        if server.execute("systemctl is-active multi-user.target")[0] == 0:
            break
        print(server.execute("systemctl list-jobs --no-pager | head -30")[1])
        time.sleep(30)
    else:
        raise Exception("multi-user.target wasn't reached within 15 minutes")

    def psql(port, db, query):
        return (
            f"sudo -u postgres psql -h /run/postgresql{'-always-on' if port == 5433 else '''} "
            f"-p {port} -d {db} -tAc \"{query}\""
        )

    with subtest("boots with both layers locked"):
        server.fail("systemctl is-active control-online.target")
        server.fail("systemctl is-active workload-online.target")
        server.fail("systemctl is-active postgresql.service")
        server.succeed('test "$(stat -c %a /var/lib/nextcloud)" = 0')

    with subtest("always-on services start"):
        for unit in [
            "sshd.service",
            "caddy.service",
            "postgresql-always-on-setup.service",
            "redis-shared.service",
            "mosquitto.service",
            "influxdb2.service",
            "grafana.service",
        ]:
            server.wait_for_unit(unit)

    with subtest("Grafana is served through Caddy with the internal CA"):
        server.wait_until_succeeds(
            "curl -sfk --resolve grafana.home.example.com:443:127.0.0.1 "
            "https://grafana.home.example.com/api/health | grep -q ok",
            timeout=300,
        )

    with subtest("Home Assistant records history in the always-on PostgreSQL"):
        server.wait_for_unit("home-assistant.service", timeout=900)
        server.wait_until_succeeds(
            psql(5433, "hass", "SELECT 1 FROM information_schema.tables WHERE table_name = 'states'") + " | grep -q 1",
            timeout=900,
        )

    with subtest("create the LUKS volumes"):
        server.succeed(
            "printf test | cryptsetup luksFormat --batch-mode --pbkdf pbkdf2 --pbkdf-force-iterations 1000 /dev/vdb",
            "printf test | cryptsetup luksOpen /dev/vdb control-luks",
            "mkfs.ext4 -q /dev/mapper/control-luks",
            "mount /dev/mapper/control-luks /mnt/control",
            "install -d -m 0700 /mnt/control/tang",
            "umount /mnt/control",
            "cryptsetup luksClose control-luks",
            "printf test | cryptsetup luksFormat --batch-mode --pbkdf pbkdf2 --pbkdf-force-iterations 1000 /dev/vdc",
            "printf test | cryptsetup luksOpen /dev/vdc workload-luks",
            "mkfs.ext4 -q /dev/mapper/workload-luks",
            "cryptsetup luksClose workload-luks",
        )

    with subtest("unlock-control brings up Tang with its keys on the control layer"):
        server.succeed("rm /var/lib/tang", "install -d -m 0 /var/lib/tang")
        server.succeed("printf test | unlock-control")
        server.wait_for_unit("control-online.target")
        server.wait_until_succeeds("curl -sf http://127.0.0.1:7500/adv | grep -q payload", timeout=120)
        server.succeed("test -L /var/lib/tang", 'test -n "$(ls /mnt/control/tang)"')

    with subtest("unlock-workload brings up the workload PostgreSQL"):
        server.succeed("printf test | unlock-workload")
        server.wait_for_unit("workload-online.target")
        server.wait_for_unit("postgresql.service")
        server.wait_for_unit("postgresql-immich-init.service")
        server.succeed("findmnt -n -o SOURCE /var/lib/postgresql | grep -q workload-luks")
        server.succeed(psql(5432, "immich", "SELECT extname FROM pg_extension WHERE extname = 'vchord'") + " | grep -q vchord")

    with subtest("lock-workload stops only the workload layer"):
        server.succeed("printf 'y\\n' | lock-workload")
        server.fail("systemctl is-active postgresql.service")
        server.fail("test -e /dev/mapper/workload-luks")
        server.succeed("systemctl is-active postgresql-always-on.service grafana.service caddy.service")
  '';
}
