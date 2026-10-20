# lib/host.nix
#
# Pure helpers for cross-host lookups in a deployment.
{ lib }:

let
  hostsWithRole = hosts: role: lib.filter (name: hosts.${name}.role == role) (lib.attrNames hosts);

  primaryHost =
    hosts: role:
    let
      matches = hostsWithRole hosts role;
    in
    if matches == [ ] then null else lib.head matches;

  hostIp = hosts: name: hosts.${name}.networking.ip;

  hostHostname = hosts: name: hosts.${name}.networking.hostname;

  hostInterface = hosts: name: hosts.${name}.networking.interface;

  # A room's hosts: lanbat.deployment.voiceRooms takes one host key or a list.
  voiceRoomHosts = hosts: lib.toList hosts;

  voiceRoomForHost =
    voiceRooms: hostKey:
    lib.findFirst (room: lib.elem hostKey (lib.toList voiceRooms.${room})) null (
      lib.attrNames voiceRooms
    );

  # Whether a haLlm (lanbat.deployment.haLlm) is served from this host's own
  # loopback, that is by its llama-cpp service, rather than by an outside API.
  # Null (no LLM) is not local.
  haLlmIsLocal =
    haLlm:
    haLlm != null
    && builtins.match "https?://(127\\.0\\.0\\.1|localhost)([/:].*)?" haLlm.baseUrl != null;

  # The assistant router's address (services/assistant-router.nix): Home
  # Assistant's agent pointed here gets the router, its local model and the
  # LLM gateway.
  routerBaseUrl = "http://127.0.0.1:8092/v1";
  haLlmIsRouter = haLlm: haLlm != null && haLlm.baseUrl == routerBaseUrl;

  # A room's screen dashboard (an Echo Show on TECHO5): its url path in Home
  # Assistant, "lanbat-screen-<room>", for the dashboard generator and the
  # device's bridge alike.
  screenDashboard =
    room:
    "lanbat-screen-"
    + lib.concatStringsSep "-" (
      lib.filter (part: lib.isString part && part != "") (builtins.split "[^a-z0-9]+" (lib.toLower room))
    );

in
{
  inherit
    haLlmIsLocal
    haLlmIsRouter
    routerBaseUrl
    screenDashboard
    hostsWithRole
    primaryHost
    hostIp
    hostHostname
    hostInterface
    voiceRoomForHost
    voiceRoomHosts
    ;
}
