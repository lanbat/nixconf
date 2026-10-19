# services/wifi-presence.nix
#
# Who is home, for the assistant router: the household's phones on the LAN
# (pkgs/wifi-presence, docs/people.md). Free-standing: it asks nothing of the
# router. It listens on the host's LAN interface for the phones listed in
# people.<key>.wifiMacs (a kernel filter drops every other frame) and sweeps
# the subnet with ARP, which sleeping phones still answer. A phone heard in
# the last 10 minutes is home; the router weighs that, mildly, into who is
# speaking. A hint, never authority.
#
# It runs where the assistant router runs, once someone has wifiMacs or the
# router learns phones from taps on a robot (its "who" page, tap.py: the
# router hands them over on the body socket). It reports to the router's body
# socket on the loopback. No state, no secrets, nothing listening.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  package = pkgs.callPackage ../pkgs/wifi-presence { };
  people = config.lanbat.deployment.people or { };
  phones = lib.filterAttrs (_: macs: macs != [ ]) (lib.mapAttrs (_: p: p.wifiMacs) people);
  hostLib = import ../lib/host.nix { inherit lib; };
  routerHere =
    hostLib.haLlmIsRouter (config.lanbat.deployment.haLlm or null)
    && config.lanbat.hasService "assistant-router";
  tapping = config.lanbat.services ? who && config.lanbat.services.who.port != null;
  enabled = routerHere && (phones != { } || tapping);
  bodyPort = config.lanbat.services.assistant-router.endpoint.port;
in
{
  lanbat.services.wifi-presence = { };

  systemd.services.wifi-presence = lib.mkIf enabled {
    description = "Who is home, from the household's phones on the LAN";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "assistant-router.service"
    ];
    wants = [ "network-online.target" ];
    environment = {
      INTERFACE = config.lanbat.network.lanInterface;
      SUBNET = config.lanbat.deployment.lanSubnet;
      PHONES = builtins.toJSON phones;
      ROUTER_BODY_URL = "ws://127.0.0.1:${toString bodyPort}/v1/body";
    };
    serviceConfig = {
      ExecStart = lib.getExe package;
      DynamicUser = true;
      # A raw socket on the LAN interface: to hear the phones and send ARP.
      AmbientCapabilities = [ "CAP_NET_RAW" ];
      CapabilityBoundingSet = [ "CAP_NET_RAW" ];
      Restart = "always";
      RestartSec = "10s";
      MemoryMax = "64M";
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      # The router on the loopback; the raw socket is not an IP socket.
      IPAddressDeny = "any";
      IPAddressAllow = [ "localhost" ];
      RestrictAddressFamilies = [
        "AF_PACKET"
        "AF_INET"
        "AF_INET6"
        "AF_UNIX"
      ];
    };
  };
}
