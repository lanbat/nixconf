# services/mosquitto.nix
#
# Mosquitto MQTT broker.
#
# Used by:
#   - Frigate → publishes detection events
#   - Home Assistant → subscribes to Frigate events, publishes automations
#   - The assistant router → the robots and who is around, by discovery
#   - Any future IoT devices on the LAN
#
# Auth model
# ----------
# Mosquitto uses a local password file.  Passwords are set via
# `mosquitto_passwd`.  This is simple, reliable, and sufficient for
# a trusted LAN.
#
# Listener binds to 0.0.0.0 (LAN-facing) so IoT devices can connect.
# The firewall restricts access to the LAN subnet only.
#
# Always-on: yes.  No NFS dependency.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  hostLib = import ../lib/host.nix { inherit lib; };
  # The assistant router beside it, publishing to Home Assistant
  # (services/assistant-router.nix, settings.homeAssistant).
  routerMqtt =
    config.lanbat.hasService "assistant-router"
    && hostLib.haLlmIsRouter (config.lanbat.deployment.haLlm or null)
    && config.lanbat.services.assistant-router.settings.homeAssistant;
in
{
  lanbat.services.mosquitto = {
    endpoint = {
      scheme = "mqtt";
      port = 1883;
    };
    extraPorts = [ 1883 ];
    # Plaintext passwords, one line each, shared with the service each one
    # belongs to (its readsSecrets): Home Assistant, Frigate and Zigbee2MQTT.
    secrets = {
      # homeassistant-bootstrap reads this to configure the MQTT integration.
      mosquitto-ha-pass = {
        group = "hass";
        mode = "0440";
      };
      mosquitto-frigate-pass = { };
      mosquitto-z2m-pass = { };
    }
    // lib.optionalAttrs (config.lanbat.hasService "person-mapper") {
      # The person mapper's broker password, read by its unit from a root
      # ExecStartPre. Declared only while that service is present.
      mosquitto-person-mapper-pass = { };
    }
    // lib.optionalAttrs routerMqtt {
      # The assistant router's, which publishes the robots and who is around
      # to Home Assistant (services/assistant-router.nix, LoadCredential).
      mosquitto-assistant-router-pass = { };
    };
  };

  services.mosquitto = {
    enable = true;

    listeners = [
      {
        port = 1883;
        address = "0.0.0.0";

        settings = {
          allow_anonymous = false;
        };

        acl = [
          # Allow all authenticated users to publish/subscribe everywhere.
          # Tighten this if you add untrusted IoT devices.
          "topic readwrite #"
        ];

        users = {
          # Home Assistant user.
          # agenix secret file must contain the plaintext password (one line).
          homeassistant = {
            passwordFile = config.lanbat.secrets.mosquitto-ha-pass.path;
            acl = [ "readwrite #" ];
          };

          # Frigate user.
          frigate = {
            passwordFile = config.lanbat.secrets.mosquitto-frigate-pass.path;
            acl = [
              "readwrite frigate/#"
              "readwrite homeassistant/#"
            ];
          };

          # Zigbee2MQTT user.
          zigbee2mqtt = {
            passwordFile = config.lanbat.secrets.mosquitto-z2m-pass.path;
            acl = [
              "readwrite zigbee2mqtt/#"
              "readwrite homeassistant/#"
            ];
          };
        }
        // lib.optionalAttrs (config.lanbat.hasService "person-mapper") {
          # The person mapper: reads Frigate's face events, writes Home Assistant
          # discovery plus the homelab room-presence topics. Least privilege.
          "person-mapper" = {
            passwordFile = config.lanbat.secrets.mosquitto-person-mapper-pass.path;
            acl = [
              "read frigate/#"
              "write homeassistant/#"
              "write homelab/#"
            ];
          };
        }
        // lib.optionalAttrs routerMqtt {
          # Its own topics, and Home Assistant's discovery and status.
          "assistant-router" = {
            passwordFile = config.lanbat.secrets.mosquitto-assistant-router-pass.path;
            acl = [
              "readwrite lanbat/#"
              "write homeassistant/#"
              "read homeassistant/status"
            ];
          };
        }
        // lib.mapAttrs (name: user: {
          passwordFile = user.passwordFile;
          acl = user.acl;
        }) config.lanbat.mosquitto.extraUsers;
      }
    ];
  };

  # Reachability is generated from the declared edges by
  # modules/wiring/policy.nix: every service that consumes mosquitto is admitted
  # and nothing else is. The rules this file used to write admitted the whole
  # LAN subnet, which nothing used — 301 client connections over fourteen days
  # were all from 127.0.0.1, which the generated drop exempts.
}
