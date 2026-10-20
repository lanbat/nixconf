# deployments/example/deploy.nix
#
# Example deployment profile for CI and contributors.
{ inputs, ... }:
{
  deployment = {
    domain = "home.example.com";
    rootDomain = "example.com";
    gatewayIp = "192.0.2.1";
    lanSubnet = "192.0.2.0/24";
    nfsIdmapdDomain = "home.lan";
    timezone = "UTC";
    phoneRegion = "GB";
    haLatitude = "51.5";
    haLongitude = "-0.1";
    haElevation = 0;
    zigbeeVendorId = "10c4";
    zigbeeProductId = "ea60";
    adminSshKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExampleExample example";
    # How hosts reach each other. "none" keeps cross-host traffic on the LAN,
    # which is also what leaving this out means. For a WireGuard mesh, set
    # provider = "wireguard-mesh" with subnet and domain, and give each host
    # an overlay block (commented out below); see docs/extensibility.md.
    overlay = {
      provider = "none";
      # subnet = "10.100.0.0/24";
      # domain = "lanbat.internal";
    };
    # The example profile is read and evaluated, never deployed, so it resolves
    # secrets to placeholders. That is what lets somebody add a service with
    # secrets and run nix flake check without holding any keys or committing an
    # empty .age file to make evaluation pass.
    secrets = {
      provider = "none";
      root = ../../secrets;
    };
    haLlm = {
      baseUrl = "https://llm.example.com/v1";
      model = "example-model";
    };
    # Or the assistant router (services/assistant-router.nix): a noise gate, the
    # local model for clear device commands, cloud models for the rest, with
    # the keys in llm-gateway-env and lanbat.services.llama-cpp.settings.model
    # = "qwen3-4b" in the server's modules:
    # haLlm = { baseUrl = "http://127.0.0.1:8092/v1"; model = "assistant"; };
    # Where the voice assistant's heavy work runs (lib/voice-compute.nix):
    # "low-spec" does it all on the server; "apple-silicon" takes the LLM from
    # a Mac on the LAN (haLlm.baseUrl), with apiKey = false if it needs none.
    voiceCompute.profile = "low-spec";
    voiceRooms = {
      "Office" = "server";
      "Living Room" = "pi-storage";
    };
    # People the assistant may recognise (docs/people.md): names only.
    people = {
      alex.name = "Alex";
      sam.name = "Sam";
    };
    # Xiaomi BLE bind keys for Home Assistant, from ha-xiaomi-ble.age.
    haXiaomiBle = true;
    # Xiaomi BLE thermometers with a clock display, for lanbatPlugins.xiaomi-clock.
    xiaomiClocks = [ "A4:C1:38:00:00:01" ];

    # LAN DNS, for hosts with lanbatPlugins.dns (modules/core/dns.nix):
    # torrent.lan redirects to torrent.home.example.com, server.lan and
    # mosquitto.lan name the host. Upstreams default to the gateway.
    dns = {
      shortSuffix = "lan";
      extraRecords.router = "192.0.2.1";
    };

    # Echo Shows on TECHO5, for the techo5-bridge service (docs/techo5.md),
    # with their keys in techo5-<name>-api-key.age. tests/service-settings.nix
    # evaluates one; here none, so Home Assistant keeps its loopback URL.
    # techo5Devices.kitchen = {
    #   address = "192.0.2.60";
    #   room = "Kitchen";
    # };

    # Android TV boxes, for lanbatPlugins.android.
    androidDevices = {
      bedroom = {
        host = "192.0.2.50";
        packages = [ "de.badaix.snapcast" ];
      };
    };
  };

  hosts = {
    server = {
      role = "server";
      system = "x86_64-linux";
      networking = {
        ip = "192.0.2.10";
        interface = "eno1";
        hostname = "server";
      };
      # overlay = {
      #   ip = "10.100.0.1";
      #   publicKey = "<from nix run .#overlay-keys>";
      #   endpoint = "192.0.2.10:51820"; # can be dialled
      # };
      disks = {
        system = "/dev/disk/by-id/example-system-disk";
      };
      plugins = [
        inputs.self.lanbatPlugins.services
        inputs.self.lanbatPlugins.android
        inputs.self.lanbatPlugins.xiaomi-clock
        inputs.self.lanbatPlugins.dns
      ];
      # Merged last, so they override anything core, the role or a plugin set.
      modules = [
        ./frigate.nix
        # Grafana alerts (disk almost full, Frigate quiet) to the webhook in
        # grafana-alert-env.age (services/grafana.nix).
        { lanbat.services.grafana.settings.alerting.enable = true; }
        # Rooms for Home Assistant devices that have none, by the name Home
        # Assistant shows (or an entity id); the generated dashboards group by
        # room (docs/dashboards.md).
        {
          lanbat.services.home-assistant.settings.deviceAreas = {
            livingroom_lamp = "Living Room";
            "switch.0x00124b0012345678" = "Hall";
          };
        }
        # Syncthing's devices and folders (services/syncthing.nix). Folder IDs
        # are the ones the other devices already use.
        {
          lanbat.services.syncthing.settings = {
            devices.laptop.id = "AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH";
            folders.example-sync = {
              label = "Sync";
              path = "/srv/storage/b/users/admin/sync/example";
              devices = [ "laptop" ];
            };
          };
        }
        # qBittorrent's categories and qBittorrent.conf (services/qbittorrent.nix).
        # Both are rewritten on every start, so nothing is set in the web UI.
        {
          lanbat.services.qbittorrent.settings = {
            categories = {
              "Music" = "";
              "Music/Albums" = "/media/b/music/albums";
              "Video" = "";
              "Video/Movies" = "/media/a/movies";
            };
            preferences.BitTorrent = {
              "Session\\MaxActiveDownloads" = 8;
              "Session\\Port" = 6881;
            };
          };
        }
      ];
    };

    pi-storage = {
      role = "storage-pi";
      system = "aarch64-linux";
      platform = "raspberry-pi-5";
      networking = {
        ip = "192.0.2.11";
        interface = "end0";
        hostname = "pi5";
      };
      # overlay = {
      #   ip = "10.100.0.2";
      #   publicKey = "<from nix run .#overlay-keys>";
      #   # No endpoint: dials out to the server.
      # };
      storage = {
        drives = {
          a = "example-storage-a";
          b = "example-storage-b";
        };
      };
      plugins = [
        inputs.self.lanbatPlugins.tv
        inputs.self.lanbatPlugins.voice
        inputs.self.lanbatPlugins.dns
      ];
    };

    # A Raspberry Pi 3 with a speaker and a microphone: a Snapcast speaker and
    # a voice satellite (docs/pi3-satellite.md).
    pi-voice = {
      role = "voice-pi";
      system = "aarch64-linux";
      platform = "raspberry-pi-3";
      networking = {
        ip = "192.0.2.12";
        interface = "eth0";
        hostname = "pi3";
      };
      plugins = [ inputs.self.lanbatPlugins.voice ];
      modules = [
        {
          lanbat.voiceSatellite.name = "Pi 3 Satellite";
          lanbat.speakers.output = "usb";
        }
      ];
    };
  };
}
