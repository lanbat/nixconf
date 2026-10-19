# lib/roles/common.nix
#
# What every built-in role shares: the hostname and static LAN address from the
# deploy entry, the firewall baseline and the state version, so a change to any
# of them is made once.
#
# A role merges this into its own configuration rather than importing it:
#
#   args@{ lib, ... }: {
#     config = lib.mkMerge [ (import ./common.nix args) { ... } ];
#   }
#
# The module system orders the definitions of a list option (nameservers,
# firewall ports, interface addresses) by where their module sits among all
# modules, and an imported module sits elsewhere than its importer. Merged in
# place, these definitions keep the exact position they had when every role
# wrote them out, next to the role's own, so nothing another module adds to the
# same lists moves. It is still a module in its own right.
{
  config,
  lib,
  ...
}:

let
  host = config.lanbat.hosts.${config.lanbat.hostKey};
  net = host.networking;
  # The port, or the bridge over it (modules/core/lan-bridge.nix).
  lan = config.lanbat.network.lanInterface;
  networkLib = import ../network.nix { inherit lib; };
  prefixLength = networkLib.prefixLengthFromCidr config.lanbat.deployment.lanSubnet;
in
{
  networking.hostName = net.hostname;

  networking = {
    useNetworkd = true;
    interfaces.${lan} = {
      useDHCP = false;
      ipv4.addresses = [
        {
          address = net.ip;
          inherit prefixLength;
        }
      ];
    };
    defaultGateway = {
      address = config.lanbat.deployment.gatewayIp;
      interface = lan;
    };
    # The gateway first; a role merges any fallback in after this.
    nameservers = [ config.lanbat.deployment.gatewayIp ];
  };

  # SSH and mDNS on every host; a role merges its own ports in after these.
  # Service ports come from modules/wiring/policy.nix, generated from the
  # declared edges.
  networking.firewall = {
    enable = true;
    allowedTCPPorts = [ 22 ];
    allowedUDPPorts = [ 5353 ];
  };

  system.stateVersion = "24.11";
}
