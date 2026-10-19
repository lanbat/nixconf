# modules/core/lan-bridge.nix
#
# The host's LAN address on a bridge instead of its port: the port (the deploy
# entry's networking.interface) and any extra ports join it, and so does a
# Wi-Fi access point (modules/core/wifi-access-point.nix). A host plugged in
# behind an extra port is then on the LAN as if through a switch.
#
# lanbat.network.lanInterface is the interface that holds the address, the
# bridge or the port; modules that listen on the LAN read it, never the
# deploy entry's interface. docs/wifi.md.
{ config, lib, ... }:

let
  inherit (lib) mkOption types;
  cfg = config.lanbat.network.bridge;
  port = config.lanbat.hosts.${config.lanbat.hostKey}.networking.interface;
in
{
  options.lanbat.network = {
    bridge = {
      enable = lib.mkEnableOption "the LAN address on a bridge over the host's port";
      name = mkOption {
        type = types.str;
        default = "br0";
        description = "The bridge's interface name.";
      };
      macAddress = mkOption {
        type = types.nullOr (types.strMatching "([0-9a-f]{2}:){5}[0-9a-f]{2}");
        default = null;
        example = "6c:2b:59:00:00:01";
        description = ''
          The bridge's MAC: the host's port's, so the LAN (the router's list,
          a DHCP reservation) keeps knowing the host. Null takes the lowest
          of its ports' MACs, which an extra port can change.
        '';
      };
      extraPorts = mkOption {
        type = types.listOf types.str;
        default = [ ];
        example = [ "enp2s0" ];
        description = ''
          More wired ports on the bridge, besides the host's own
          (networking.interface): a host plugged into one is on the LAN as if
          through a switch, and offline while this host restarts.
        '';
      };
    };
    lanInterface = mkOption {
      type = types.str;
      readOnly = true;
      description = "The interface that holds the host's LAN address: the bridge, or the port.";
    };
  };

  config = lib.mkMerge [
    { lanbat.network.lanInterface = if cfg.enable then cfg.name else port; }
    (lib.mkIf cfg.enable {
      assertions = [
        {
          assertion = !(lib.elem port cfg.extraPorts) && cfg.name != port;
          message = "lanbat.network.bridge: the host's port ${port} is on the bridge already; list only other ports in extraPorts, and name the bridge differently.";
        }
      ];
      systemd.network.netdevs."20-${cfg.name}".netdevConfig = {
        Kind = "bridge";
        Name = cfg.name;
        # The port's, given; otherwise the kernel takes the lowest of the
        # ports' MACs.
        MACAddress = if cfg.macAddress != null then cfg.macAddress else "none";
      };
      systemd.network.networks = lib.listToAttrs (
        map (p: {
          name = "30-${p}";
          value = {
            matchConfig.Name = p;
            networkConfig.Bridge = cfg.name;
            linkConfig.RequiredForOnline = "enslaved";
          };
        }) ([ port ] ++ cfg.extraPorts)
      );
    })
  ];
}
