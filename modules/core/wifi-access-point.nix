# modules/core/wifi-access-point.nix
#
# A Wi-Fi access point on a USB dongle (MediaTek MT7921AU and the like), on
# the host's LAN bridge (modules/core/lan-bridge.nix): the home network's own
# name and password, so phones, laptops and the robots roam onto it where the
# router's signal is weak. The LAN's DHCP and DNS serve its clients; nothing
# is routed here. Only for a wired host with power to spare: a Pi 3 is too
# weak for it. docs/wifi.md.
{ config, lib, ... }:

let
  inherit (lib) mkOption types;
  cfg = config.lanbat.wifi.accessPoint;
  bridge = config.lanbat.network.bridge;
in
{
  options.lanbat.wifi.accessPoint = {
    enable = lib.mkEnableOption "a Wi-Fi access point extending the home network";
    interface = mkOption {
      type = types.str;
      example = "wlp0s20f0u1";
      description = "The dongle's interface (`ip link`; its name follows the USB port).";
    };
    ssid = mkOption {
      type = types.str;
      description = "The home network's name, the same as the router's so devices roam between them.";
    };
    band = mkOption {
      type = types.enum [
        "2g"
        "5g"
      ];
      default = "2g";
      description = "2.4 GHz reaches farther through walls; 5 GHz is faster near the access point.";
    };
    channel = mkOption {
      type = types.ints.positive;
      default = 6;
      description = "A channel the router doesn't use, so the two don't share airtime (2.4 GHz: 1, 6 or 11).";
    };
    countryCode = mkOption {
      type = types.strMatching "[A-Z]{2}";
      example = "GB";
      description = "The country, for its radio rules (ISO 3166-1 alpha-2).";
    };
    security = mkOption {
      type = types.enum [
        "wpa2-sha1"
        "wpa2-sha256"
      ];
      default = "wpa2-sha1";
      description = "As the router has it: WPA2-Personal, which every phone and the robots support.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = bridge.enable;
        message = "lanbat.wifi.accessPoint puts its clients on the LAN through the host's bridge: set lanbat.network.bridge.enable.";
      }
    ];

    # The home network's password, one line (the router's), for hostapd.
    lanbat.hostSecrets.wifi-access-point = {
      owner = "root";
      mode = "0400";
    };

    services.hostapd = {
      enable = true;
      radios.${cfg.interface} = {
        inherit (cfg) band channel countryCode;
        networks.${cfg.interface} = {
          inherit (cfg) ssid;
          authentication = {
            mode = cfg.security;
            wpaPasswordFile = config.lanbat.secrets.wifi-access-point.path;
          };
          settings.bridge = bridge.name;
        };
      };
    };
    # hostapd adds the radio to the bridge itself; networkd leaves it alone.
    systemd.network.networks."30-${cfg.interface}" = {
      matchConfig.Name = cfg.interface;
      linkConfig.Unmanaged = true;
    };
  };
}
