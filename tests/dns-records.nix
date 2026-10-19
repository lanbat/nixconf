# tests/dns-records.nix
#
# Pure evaluation of the DNS names (modules/core/dns.nix) and the short-name
# redirects (modules/wiring/caddy.nix):
#
#   - a host name, or an extra record, clashing with a service name is an
#     evaluation error naming both claims;
#   - shortSuffix set: every Caddy subdomain gets an HTTPS and an HTTP
#     redirect to <subdomain>.<domain>; unset: no short vhosts at all.
{ lib, pkgs }:

let
  hosts = {
    server = {
      role = "server";
      networking = {
        ip = "192.0.2.10";
        interface = "eth0";
        hostname = "server";
      };
    };
  };

  services = {
    caddy = {
      subdomain = "ca";
      auth = "none";
      caddy.extraConfig = "file_server";
    };
    qbittorrent = {
      subdomain = "torrent";
      port = 8090;
    };
  };

  endpoints = {
    caddy = {
      hosts = [ "server" ];
      web.subdomain = "ca";
    };
    qbittorrent = {
      hosts = [ "server" ];
      web.subdomain = "torrent";
    };
    mosquitto = {
      hosts = [ "server" ];
      web = null;
    };
  };

  evalHost =
    {
      dns ? { },
      hostname ? "server",
    }:
    (lib.nixosSystem {
      modules = [
        ../modules/core/settings.nix
        ../modules/core/host-context.nix
        ../modules/core/services.nix
        ../modules/core/auth.nix
        ../modules/core/overlay.nix
        ../modules/wiring/secrets.nix
        ../modules/wiring/caddy.nix
        (import ../lib/overlay-providers.nix).none
        ./lib/age-option-stub.nix
        ../modules/core/lan-bridge.nix
        ../modules/core/dns.nix
        {
          boot.isContainer = true;
          nixpkgs.hostPlatform = "x86_64-linux";
          system.stateVersion = "25.11";
          lanbat.profile = "test";
          lanbat.hostKey = "server";
          lanbat.hosts = lib.recursiveUpdate hosts { server.networking.hostname = hostname; };
          lanbat.services = services;
          lanbat.endpoints = endpoints;
          lanbat.deployment = {
            domain = "home.test";
            gatewayIp = "192.0.2.1";
            lanSubnet = "192.0.2.0/24";
            secrets = {
              provider = "none";
              root = ../secrets;
            };
            inherit dns;
          };
        }
      ];
    }).config;

  lanbatErrors =
    cfg:
    map (a: a.message) (
      lib.filter (a: !a.assertion && lib.hasPrefix "lanbat:" a.message) cfg.assertions
    );
  hasError = cfg: needle: lib.any (lib.hasInfix needle) (lanbatErrors cfg);

  withShort = evalHost { dns.shortSuffix = "lan"; };
  withoutShort = evalHost { };
  hostClash = evalHost { hostname = "torrent"; };
  extraClash = evalHost { dns.extraRecords.mosquitto = "192.0.2.99"; };

  vhosts = cfg: cfg.services.caddy.virtualHosts;

  expect = name: ok: if ok then null else name;

  cases = [
    (expect "a valid profile has no DNS errors" (lanbatErrors withShort == [ ]))
    (expect "a host named like a subdomain is rejected" (
      hasError hostClash "the DNS name torrent is claimed more than once"
    ))
    (expect "an extra record named like a service is rejected" (
      hasError extraClash "the DNS name mosquitto is claimed more than once"
    ))
    (expect "a short HTTPS name redirects to the full one" (
      lib.hasInfix "redir https://torrent.home.test{uri} 308" (vhosts withShort)."torrent.lan".extraConfig
    ))
    (expect "a short HTTP name redirects straight to the full HTTPS one" (
      lib.hasInfix "redir https://torrent.home.test{uri} 308"
        (vhosts withShort)."http://torrent.lan".extraConfig
    ))
    (expect "every Caddy subdomain gets a short name" ((vhosts withShort) ? "ca.lan"))
    (expect "the full name still serves the service" (
      lib.hasInfix "reverse_proxy localhost:8090" (vhosts withShort)."torrent.home.test".extraConfig
    ))
    (expect "no short suffix, no short vhosts" (
      lib.attrNames (vhosts withoutShort) == [
        "ca.home.test"
        "torrent.home.test"
      ]
    ))
    (expect "no short suffix, no short zone" (
      !(lib.hasInfix "lan:53" withoutShort.services.coredns.config)
    ))
  ];

  failures = lib.filter (x: x != null) cases;
in
pkgs.runCommand "dns-records-check" { } ''
  if [ ${toString (lib.length failures)} -ne 0 ]; then
    echo "dns record checks failed:" >&2
    ${lib.concatStringsSep "\n" (map (msg: "echo \"  - ${msg}\" >&2") failures)}
    exit 1
  fi
  touch $out
''
