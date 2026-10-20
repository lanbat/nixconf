# modules/core/settings.nix
#
# Deployment and host settings. Values are injected from deploy.nix by
# lib/mkHost.nix; none have defaults except optional features.
{ lib, ... }:

let
  inherit (lib) mkOption types;

  ipv4 = types.strMatching "([0-9]{1,3}\\.){3}[0-9]{1,3}";

  hostSubmodule = types.submodule {
    options = {
      role = mkOption {
        # Not an enum: a plugin can add a role (hostRoles), and every host sees
        # the roles of the others. lib/validate-deploy.nix rejects a role that
        # neither lib/roles.nix nor the host's plugins declare.
        type = types.str;
        example = "storage-pi";
        description = "Infrastructure role of this host: server, storage-pi, voice-pi or one a plugin declares.";
      };

      networking = {
        ip = mkOption {
          type = ipv4;
          description = "Static IPv4 address of this host.";
        };
        interface = mkOption {
          type = types.str;
          description = "Network interface that gets the static address.";
        };
        hostname = mkOption {
          type = types.str;
          description = "Hostname of this host.";
        };
      };

      overlay = mkOption {
        type = types.nullOr (
          types.submodule {
            options = {
              ip = mkOption {
                type = types.str;
                example = "10.100.0.1";
                description = "Address this host holds on the overlay.";
              };
              publicKey = mkOption {
                type = types.str;
                description = "Public key other hosts encrypt to. Not a secret.";
              };
              endpoint = mkOption {
                type = types.nullOr types.str;
                default = null;
                example = "vpn.example.com:51820";
                description = ''
                  Where this host can be dialled, when it can be. A host without
                  one keeps a path open toward those that have one, so a
                  rendezvous host is just a host with an endpoint rather than a
                  special case.
                '';
              };
            };
          }
        );
        default = null;
        description = "This host's place on the overlay, or null if it does not join one.";
      };

      pluginNames = mkOption {
        type = types.listOf types.str;
        default = [ ];
        internal = true;
        description = ''
          The names of the plugins this host's deploy entry lists (e.g.
          "lanbat-tv"), so another host can tell statically what it runs.
        '';
      };

      services = mkOption {
        type = types.listOf types.str;
        default = [ ];
        example = [
          "caddy"
          "frigate"
        ];
        description = ''
          Services this host runs, by name, resolved through the service
          registry in modules/core/service-registry.nix. A host with an empty
          list runs no lanbat service.
        '';
      };

      disks = mkOption {
        type = types.attrsOf types.str;
        default = { };
        description = "Role-specific disks. Server role uses disks.system.";
      };

      storage = mkOption {
        type = types.submodule {
          options = {
            drives = mkOption {
              type = types.attrsOf types.str;
              default = { };
              description = "Storage drive by-id filenames (without /dev/disk/by-id/ prefix).";
            };
          };
        };
        default = { };
        description = "Storage configuration for storage-pi hosts.";
      };
    };
  };
in
{
  options.lanbat = {
    profile = mkOption {
      type = types.str;
      internal = true;
      description = "Deployment profile name (site) this host belongs to.";
    };

    hostKey = mkOption {
      type = types.str;
      internal = true;
      description = "This host's key in deploy.nix hosts.";
    };

    deployment = {
      domain = mkOption {
        type = types.str;
        example = "home.example.com";
        description = "Base service domain. DNS for *.<domain> must point to the server.";
      };

      rootDomain = mkOption {
        type = types.str;
        example = "example.com";
        description = "Root DNS zone, typically the parent of deployment.domain.";
      };

      gatewayIp = mkOption {
        type = ipv4;
        example = "192.168.1.1";
        description = "Default gateway (usually the router).";
      };

      lanSubnet = mkOption {
        type = types.strMatching "([0-9]{1,3}\\.){3}[0-9]{1,3}/[0-9]{1,2}";
        example = "192.168.1.0/24";
        description = "LAN subnet in CIDR notation.";
      };

      nfsIdmapdDomain = mkOption {
        type = types.str;
        example = "home.lan";
        description = "NFSv4 ID mapping domain, identical on all NFS hosts.";
      };

      timezone = mkOption {
        type = types.str;
        example = "Europe/London";
        description = "System timezone of all hosts.";
      };

      phoneRegion = mkOption {
        type = types.strMatching "[A-Z]{2}";
        example = "GB";
        description = "ISO 3166-1 alpha-2 country code for phone number formatting.";
      };

      haLatitude = mkOption {
        type = types.str;
        example = "51.5";
        description = "Home latitude in decimal degrees.";
      };

      haLongitude = mkOption {
        type = types.str;
        example = "-0.1";
        description = "Home longitude in decimal degrees.";
      };

      haElevation = mkOption {
        type = types.int;
        example = 50;
        description = "Home elevation above sea level in metres.";
      };

      zigbeeVendorId = mkOption {
        type = types.strMatching "[0-9a-f]{4}";
        example = "10c4";
        description = "USB vendor ID of the Zigbee dongle.";
      };

      zigbeeProductId = mkOption {
        type = types.strMatching "[0-9a-f]{4}";
        example = "ea60";
        description = "USB product ID of the Zigbee dongle.";
      };

      adminSshKey = mkOption {
        type = types.strMatching "(ssh-|ecdsa-|sk-).+";
        example = "ssh-ed25519 AAAAC3Nza... admin@workstation";
        description = "SSH public key of the admin user on all hosts.";
      };

      overlay = mkOption {
        type = types.submodule {
          options = {
            provider = mkOption {
              type = types.str;
              default = "none";
              example = "wireguard-mesh";
              description = ''
                Which implementation answers the overlay contract. "none" keeps
                cross-host traffic on the LAN, which is what a profile that says
                nothing gets.
              '';
            };
            subnet = mkOption {
              type = types.nullOr types.str;
              default = null;
              example = "10.100.0.0/24";
              description = "Address range the overlay uses, when it has one.";
            };
            domain = mkOption {
              type = types.nullOr types.str;
              default = null;
              example = "lanbat.internal";
              description = "Suffix overlay names are resolved under, when the provider uses one.";
            };
          };
        };
        default = { };
        description = ''
          How hosts reach each other. Optional: absent means no overlay, and
          cross-host traffic stays on the LAN as it does today.
        '';
      };

      secrets = mkOption {
        type = types.submodule (
          { config, ... }:
          {
            options = {
              provider = mkOption {
                type = types.enum [
                  "agenix"
                  "sops"
                  "none"
                ];
                example = "agenix";
                description = ''
                  Backend that decrypts this profile's secrets. none resolves
                  every requirement to a throwaway file, so evaluation and the
                  flake checks need no encrypted files at all.
                '';
              };
              root = mkOption {
                type = types.path;
                example = lib.literalExpression "./secrets";
                description = ''
                  Directory holding this profile's encrypted secrets, resolved
                  relative to the profile rather than to lanbat itself, so a fork
                  keeps its own secrets outside this repository.
                '';
              };
              caCertificate = mkOption {
                type = types.path;
                default = config.root + "/caddy-ca-root.crt";
                defaultText = lib.literalExpression ''root + "/caddy-ca-root.crt"'';
                description = ''
                  Public root certificate of the internal CA that Caddy issues
                  from. Its private key is the caddy-ca-root-key secret, so the
                  pair comes from the same profile. Hosts and clients that trust
                  the internal CA take it from here.
                '';
              };
            };
          }
        );
        description = "Where this profile's secrets come from and how they are decrypted.";
      };

      immich.adminEmail = mkOption {
        type = types.str;
        example = "alice@example.com";
        description = "Email for the bootstrap Immich admin.";
      };

      haLlm = mkOption {
        type = types.nullOr (
          types.submodule (
            { config, ... }:
            {
              options = {
                baseUrl = mkOption {
                  type = types.strMatching "https?://.+";
                  example = "https://api.runpod.ai/v2/<endpoint-id>/openai/v1";
                  description = "Base URL of the OpenAI-compatible API.";
                };
                model = mkOption {
                  type = types.str;
                  example = "qwen3-8b-ha";
                  description = "Name of the model the API serves.";
                };
                apiKey = mkOption {
                  type = types.bool;
                  default = !(import ../../lib/host.nix { inherit lib; }).haLlmIsLocal config;
                  defaultText = lib.literalExpression "baseUrl is not on the loopback";
                  description = ''
                    Whether the API needs a key, read from the ha-llm-api-key
                    secret. Off for the loopback; set it false for a server on the
                    LAN that takes none, such as the Mac of the apple-silicon
                    profile.
                  '';
                };
              };
            }
          )
        );
        default = null;
        description = "Home Assistant conversation agent LLM. null uses HA's own agent.";
      };

      voiceCompute.profile = mkOption {
        type = types.enum (import ../../lib/voice-compute.nix { inherit lib; }).names;
        default = "low-spec";
        description = ''
          Where the voice assistant's heavy work runs; lib/voice-compute.nix
          has each profile's goals. "low-spec": Home Assistant's server does
          everything, a small LLM included (llama.cpp on the loopback, or
          another API in haLlm). "apple-silicon": an Apple Silicon Mac on the
          LAN serves the LLM (haLlm points at it), and the server spends the
          freed cores on a larger speech-to-text model.
        '';
      };

      haXiaomiBle = mkOption {
        type = types.bool;
        default = false;
        description = ''
          Whether Home Assistant adds Xiaomi BLE devices from bind keys in the
          ha-xiaomi-ble secret, one "<MAC> <bindkey> [title]" line per device.
          Needs that secret; see secrets/README.md.
        '';
      };

      parkingGuard = mkOption {
        type = types.nullOr (
          types.submodule {
            options = {
              siteId = mkOption {
                type = types.str;
                description = "JustPark site / location identifier for API sync.";
              };
              cameras = mkOption {
                type = types.listOf types.str;
                default = [ "c1" ];
                description = "Frigate camera names to evaluate for LPR.";
              };
              graceMinutes = mkOption {
                type = types.ints.positive;
                default = 5;
                description = "Minutes before booking start / after end to still treat as authorized.";
              };
              cooldownMinutes = mkOption {
                type = types.ints.positive;
                default = 30;
                description = "Minutes between repeat alerts for the same plate.";
              };
              minScore = mkOption {
                type = types.float;
                default = 0.8;
                description = "Minimum Frigate LPR confidence score to evaluate.";
              };
              residentPlates = mkOption {
                type = types.listOf types.str;
                default = [ ];
                description = "Always-authorized resident plates (manual allowlist).";
              };
              syncIntervalMinutes = mkOption {
                type = types.ints.positive;
                default = 5;
                description = "How often to poll JustPark / re-import CSV.";
              };
            };
          }
        );
        default = null;
        description = "Parking guard settings when lanbat-justpark-parking is enabled on the server.";
      };

      androidDevices = mkOption {
        type = types.attrsOf types.anything;
        default = { };
        description = ''
          Android TV boxes to provision, for the lanbat-android plugin.  The
          schema is declared by modules/server/android-devices.nix; this option
          only carries the values from deploy.nix, which is where real device
          addresses belong.  Type checking happens at the androidDevices option.
        '';
      };

      people = mkOption {
        type = types.attrsOf (
          types.submodule {
            options.name = mkOption {
              type = types.strMatching "[^\n]{1,40}";
              description = "What the assistant calls this person.";
            };
            options.bleBeacon = mkOption {
              type = types.nullOr (
                types.strMatching "[0-9A-Fa-f]{8}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{12}"
              );
              default = null;
              example = "a1b2c3d4-e5f6-0718-293a-4b5c6d7e8f90";
              description = ''
                The UUID the Home Assistant app on this person's Android phone
                transmits as an iBeacon (Companion app → Manage sensors → BLE
                transmitter), for the room-presence plugin. iPhones are known by
                their IRK instead (docs/people.md).
              '';
            };
            options.wifiMacs = mkOption {
              type = types.listOf (types.strMatching "([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}");
              default = [ ];
              example = [ "3a:1f:00:aa:bb:01" ];
              description = ''
                This person's phones' Wi-Fi addresses on the home network, for
                the wifi-presence service: the fixed private address the phone
                keeps for this network (Android: Wi-Fi → network → Privacy;
                iPhone: Wi-Fi → (i) → Private Wi-Fi Address set to Fixed).
                docs/people.md.
              '';
            };
          }
        );
        default = { };
        example = {
          alex.name = "Alex";
          sam.name = "Sam";
        };
        description = ''
          The people the assistant may recognise (by voice, by face at a robot,
          by phone over Bluetooth or on the home network), keyed by a short
          identifier. The keys are the
          only identities that travel between the satellites, the robot and the
          assistant router; names, never secrets or voiceprints. Recognition is a
          hint for what the assistant says, never authority over anything
          (docs/people.md).
        '';
      };

      xiaomiClocks = mkOption {
        type = types.listOf (types.strMatching "([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}");
        default = [ ];
        example = [ "A4:C1:38:00:00:01" ];
        description = ''
          Bluetooth addresses of Xiaomi BLE thermometers with a clock display
          (LYWSD02/LYWSD02MMC), for the lanbat-xiaomi-clock plugin.  They cannot
          set their own time and ship on UTC+8, so a timer writes it over BLE.
        '';
      };

      techo5Devices = mkOption {
        type = types.attrsOf (
          types.submodule (
            let
              profile = types.submodule {
                options = {
                  view = mkOption {
                    type = types.nullOr (types.strMatching "[a-z0-9_-]{1,40}");
                    default = null;
                    description = "The view of the device's dashboard to show (its path); null: the person's key.";
                  };
                  glance = mkOption {
                    type = types.listOf (types.strMatching "[a-z_]+\\.[a-z0-9_]+");
                    default = [ ];
                    example = [ "sensor.washer_status" ];
                    description = ''
                      Entities shown as chips along the foot of the clock (up to 8).
                      Empty: where the other people are.
                    '';
                  };
                  calendars = mkOption {
                    type = types.listOf (types.strMatching "calendar\\.[a-z0-9_]+");
                    default = [ ];
                    description = "The calendars the device shows; empty: the household's.";
                  };
                  immichPerson = mkOption {
                    type = types.str;
                    default = "";
                    description = "The person, as Immich names them, whose photos the slideshow shows; empty: everyone's.";
                  };
                };
              };
            in
            {
              options = {
                address = mkOption {
                  type = ipv4;
                  description = "The device's address on the LAN (give it a fixed lease).";
                };
                room = mkOption {
                  type = types.strMatching "[^\n]{1,64}";
                  example = "Kitchen";
                  description = ''
                    The Home Assistant area it is in, as voiceRooms names rooms. The
                    device is the assistant router's body for this room.
                  '';
                };
                playerName = mkOption {
                  type = types.nullOr types.str;
                  default = null;
                  example = "Echo Show 8";
                  description = ''
                    The device's own name, as Music Assistant shows its Sendspin
                    player, so that player goes in the room too. Null leaves the
                    player where it is.
                  '';
                };
                installCa = mkOption {
                  type = types.bool;
                  default = true;
                  description = ''
                    Put the profile's root CA (secrets.caCertificate) among the
                    certificates the device trusts, so it checks Home Assistant's
                    certificate, dashboards included. TECHO5 has no setting for it and
                    a read-only root, so the bridge does it over SSH after each of the
                    device's boots: it gives the device its key (techo5-ssh-key) and
                    the admin's, and turns the device's SSH on, which lets root in
                    with those keys (docs/techo5.md).
                  '';
                };
                insecureTls = mkOption {
                  type = types.bool;
                  default = false;
                  description = ''
                    Turn on the device's "Skip certificate checks" instead: for a
                    device that can't have the CA (installCa false). Encrypted, but
                    the device no longer checks who answers, so someone on the LAN
                    who impersonates the server could get its Home Assistant token.
                    It doesn't cover a drawn dashboard's websocket (docs/techo5.md).
                  '';
                };
                screenOffAfter = mkOption {
                  type = types.ints.between 0 120;
                  default = 5;
                  description = ''
                    Minutes the room must be empty (nobody in front of the device and
                    nobody the assistant router places there) and the device idle
                    (no conversation, music, video or ringing) before its screen
                    goes off. 0 never turns it off.
                  '';
                };
                settings = mkOption {
                  type = types.attrsOf (
                    types.oneOf [
                      types.bool
                      types.int
                      types.float
                      types.str
                    ]
                  );
                  default = { };
                  example = {
                    screen_dashboard_idle = false;
                  };
                  description = ''
                    The device's own settings, by ESPHome object ID, over lanbat's
                    defaults (services/techo5-bridge.nix). The bridge puts them back
                    when they change.
                  '';
                };
                profiles = mkOption {
                  type = types.attrsOf profile;
                  default = { };
                  description = ''
                    What the screen shows while the assistant router places this one
                    person (a key of people) alone in the room.
                  '';
                };
                household = mkOption {
                  type = profile;
                  default = { };
                  description = "What it shows otherwise: nobody, someone unknown or several people.";
                };
              };
            }
          )
        );
        default = { };
        example = {
          kitchen = {
            address = "192.0.2.60";
            room = "Kitchen";
          };
        };
        description = ''
          Amazon Echo Shows running TECHO5, for the techo5-bridge service
          (docs/techo5.md). Each needs its ESPHome encryption key in
          techo5-<name>-api-key.age.
        '';
      };

      dns = mkOption {
        type = types.submodule {
          options = {
            shortSuffix = mkOption {
              type = types.nullOr (types.strMatching "[a-z0-9-]+(\\.[a-z0-9-]+)*");
              default = null;
              example = "lan";
              description = ''
                Suffix of the short LAN names: <subdomain>.<shortSuffix> for every
                web service, <service>.<shortSuffix> for every other service on
                one host, and <hostname>.<shortSuffix> for every host. Caddy
                redirects a short web name to <subdomain>.<domain>, which stays
                the name logins and apps use. Null means no short names.
              '';
            };
            upstreams = mkOption {
              type = types.listOf types.str;
              default = [ ];
              example = [ "192.0.2.1" ];
              description = ''
                Resolvers CoreDNS forwards every other name to, in any form the
                forward plugin accepts (an address, address:port, tls://...).
                Empty means the gateway.
              '';
            };
            extraRecords = mkOption {
              type = types.attrsOf ipv4;
              default = { };
              example = {
                router = "192.0.2.1";
                printer = "192.0.2.30";
              };
              description = ''
                Further LAN names, served under deployment.domain and, when set,
                shortSuffix. A name must not clash with a host, service or
                subdomain name.
              '';
            };
          };
        };
        default = { };
        description = ''
          LAN DNS, for hosts with the lanbat-dns plugin (modules/core/dns.nix).
          Profile-wide, so that every CoreDNS host answers alike.
        '';
      };

      voiceRooms = mkOption {
        type = types.attrsOf (types.either types.str (types.listOf types.str));
        default = { };
        example = {
          "Office" = [
            "server"
            "pi-storage"
          ];
          "Living Room" = "pi-voice";
        };
        description = ''
          Maps Home Assistant area names to the host keys of the voice satellites
          in them: one host, or a list when a room has several. Satellites and
          their hosts' music players are put in that area, and voice commands
          act on the area of the satellite that heard them.
        '';
      };

      primaryServer = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Host key for the primary server. Required when multiple server-role hosts exist.";
      };

      primaryStorage = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Host key for the primary storage-pi. Required when multiple storage-pi hosts exist.";
      };

      serverIp = mkOption {
        type = types.nullOr ipv4;
        readOnly = true;
        description = "IPv4 of primaryServer, computed from lanbat.hosts.";
      };

      storageIp = mkOption {
        type = types.nullOr ipv4;
        readOnly = true;
        description = "IPv4 of primaryStorage, computed from lanbat.hosts.";
      };

      storageHostname = mkOption {
        type = types.nullOr types.str;
        readOnly = true;
        description = "Hostname of primaryStorage, computed from lanbat.hosts.";
      };
    };

    hosts = mkOption {
      type = types.attrsOf hostSubmodule;
      description = "All hosts in this deployment.";
    };

    mosquitto = {
      extraUsers = mkOption {
        type = types.attrsOf (
          types.submodule {
            options = {
              passwordFile = mkOption {
                type = types.path;
                description = "Plaintext MQTT password file for this user.";
              };
              acl = mkOption {
                type = types.listOf types.str;
                default = [ "readwrite #" ];
                description = "Mosquitto ACL lines for this user.";
              };
            };
          }
        );
        default = { };
        description = "Extra Mosquitto users registered by plugins.";
      };
    };
  };
}
