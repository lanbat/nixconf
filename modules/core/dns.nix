# modules/core/dns.nix
#
# CoreDNS as the LAN resolver, for hosts with the lanbat-dns plugin.
#
# Names
# -----
# Every record is computed from the profile-wide service table
# (config.lanbat.endpoints) and lanbat.hosts, which every host evaluates alike,
# so two CoreDNS hosts serve identical zones without replicating anything:
#
#   <subdomain>  → the host whose Caddy serves it (every web service)
#   <service>    → the host it runs on (services without a subdomain on one
#                  host: Samba, Mosquitto, InfluxDB...), for clients that are
#                  not web browsers
#   <hostname>   → that host
#   deployment.dns.extraRecords
#
# each under deployment.domain and, when set, deployment.dns.shortSuffix.
# Caddy redirects a short web name to the full one (modules/wiring/caddy.nix),
# so logins and apps keep using <subdomain>.<domain>.
#
# Service-to-service traffic does not depend on this: endpoints resolve to
# addresses at evaluation time (lanbat.endpointHost). OIDC calls to
# auth.<domain> do resolve through DNS, from containers through the gateway
# (Podman drops loopback resolvers), which is why the router keeps its
# *.<domain> record (docs/deployment-checklist.md).
#
# Zones
# -----
# The short suffix is answered here alone, as an authoritative zone: a name
# not listed is NXDOMAIN.
# Under the domain, a name not listed falls through to the upstreams, so any
# public record there still resolves. Everything else is forwarded.
#
# Monitoring
# ----------
# The plugin named prometheus only serves a metrics page on the loopback; this
# host's Telegraf reads it. The server's Telegraf also queries every CoreDNS
# host (inputs.dns_query in services/telegraf.nix), which is the health check:
# it records whether each answers, with what, and how fast.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  lanbat = config.lanbat;
  dns = lanbat.deployment.dns;
  domain = lanbat.deployment.domain;
  short = dns.shortSuffix;
  net = lanbat.hosts.${lanbat.hostKey}.networking;
  endpoints = lanbat.endpoints;

  ipOf = hostKey: lanbat.hosts.${hostKey}.networking.ip;

  # Where web names point: the Caddy host, or the primary server when several
  # hosts run Caddy.
  caddyHosts = lib.sort (a: b: a < b) (endpoints.caddy or { hosts = [ ]; }).hosts;
  caddyHost =
    if lib.elem lanbat.deployment.primaryServer caddyHosts then
      lanbat.deployment.primaryServer
    else
      lib.head caddyHosts;

  subdomainOf = entry: if (entry.web or null) == null then null else entry.web.subdomain;

  webRecords = lib.optionals (caddyHosts != [ ]) (
    map (sub: {
      name = sub;
      ip = ipOf caddyHost;
      from = "subdomain";
    }) (lib.unique (lib.filter (s: s != null) (lib.mapAttrsToList (_: subdomainOf) endpoints)))
  );

  serviceRecords = lib.mapAttrsToList (name: entry: {
    inherit name;
    ip = ipOf (lib.head entry.hosts);
    from = "service";
  }) (lib.filterAttrs (_: entry: subdomainOf entry == null && lib.length entry.hosts == 1) endpoints);

  hostRecords = lib.mapAttrsToList (_: host: {
    name = host.networking.hostname;
    ip = host.networking.ip;
    from = "host";
  }) lanbat.hosts;

  extraRecords = lib.mapAttrsToList (name: ip: {
    inherit name ip;
    from = "extraRecords";
  }) dns.extraRecords;

  records = webRecords ++ serviceRecords ++ hostRecords ++ extraRecords;

  clashes = lib.filterAttrs (_: rs: lib.length rs > 1) (lib.groupBy (r: r.name) records);

  hostsFile =
    suffix:
    pkgs.writeText "coredns-${suffix}.hosts" (
      lib.concatMapStrings (r: "${r.ip} ${r.name}.${suffix}\n") (lib.sort (a: b: a.name < b.name) records)
    );

  # The short suffix is a zone of its own, served from a zone file: with an SOA
  # the file plugin can answer NXDOMAIN for a name it does not have, where the
  # hosts plugin answers SERVFAIL, which sends clients on to the next resolver.
  # Its name servers are the CoreDNS hosts, which have host records here.
  dnsHosts = lib.sort (a: b: a < b) (endpoints.coredns or { hosts = [ ]; }).hosts;
  nameServers = map (host: lanbat.hosts.${host}.networking.hostname) (
    if dnsHosts == [ ] then [ lanbat.hostKey ] else dnsHosts
  );
  zoneFile =
    suffix:
    pkgs.writeText "coredns-${suffix}.zone" (
      ''
        $ORIGIN ${suffix}.
        $TTL 300
        @ IN SOA ${lib.head nameServers}.${suffix}. hostmaster.${suffix}. 1 3600 600 86400 300
      ''
      + lib.concatMapStrings (ns: "@ IN NS ${ns}.${suffix}.\n") nameServers
      + lib.concatMapStrings (r: "${r.name} IN A ${r.ip}\n") (lib.sort (a: b: a.name < b.name) records)
    );

  resolved = config.services.resolved.enable;
  delegate = ''
    [Delegate]
    DNS=${
      lib.concatStringsSep " " (
        [ "127.0.0.1" ] ++ map ipOf (lib.filter (host: host != lanbat.hostKey) dnsHosts)
      )
    }
    Domains=${
      lib.concatMapStringsSep " " (zone: "~${zone}") ([ domain ] ++ lib.optional (short != null) short)
    }
  '';

  upstreams = if dns.upstreams == [ ] then [ lanbat.deployment.gatewayIp ] else dns.upstreams;

  # A server block, its directives indented and blank lines dropped.
  serverBlock =
    zones: body:
    "${zones} {\n"
    + lib.concatMapStrings (line: "  ${line}\n") (
      lib.filter (line: lib.trim line != "") (lib.splitString "\n" body)
    )
    + "}\n";

  # Every server block answers the LAN and this host only.
  common = ''
    bind ${net.ip} 127.0.0.1
    acl {
      allow net ${lanbat.deployment.lanSubnet} 127.0.0.0/8
      block
    }
    errors
    prometheus 127.0.0.1:9153
  '';

  corefile =
    lib.optionalString (short != null) (
      serverBlock "${short}:53" ''
        ${common}
        file ${zoneFile short} ${short}
      ''
    )
    + serverBlock ".:53" ''
      ${common}
      hosts ${hostsFile domain} ${domain} {
        ttl 300
        no_reverse
        fallthrough
      }
      forward . ${lib.concatStringsSep " " upstreams}
      cache 3600
      loop
    '';

in
{
  services.coredns = {
    enable = true;
    config = corefile;
  };

  # Bind needs the LAN address present, and a burst of failures at boot must
  # not exhaust the start limit.
  systemd.services.coredns = {
    wants = [ "network-online.target" ];
    after = [ "network-online.target" ];
    serviceConfig.RestartSec = "2s";
  };

  lanbat.services.coredns.extraPorts = [
    53
    9153 # metrics, read by Telegraf
  ];

  networking.firewall.interfaces.${config.lanbat.network.lanInterface} = {
    allowedUDPPorts = [ 53 ];
    allowedTCPPorts = [ 53 ];
  };

  # This host resolves through its own CoreDNS first; the gateway (and the
  # server's public fallback) stay behind it for when it is down.
  networking.nameservers = lib.mkBefore [ "127.0.0.1" ];

  # systemd-resolved sends a name to the DNS servers of the interface it
  # learnt them on (a router's IPv6 advertisement, say) in preference to the
  # global ones, and stays on a fallback server once it has switched to one,
  # for example at boot before CoreDNS listens. Either way the profile's names
  # went to a resolver that does not have them. A delegation is a scope of its
  # own: names under the domain and the short suffix always go to this host's
  # CoreDNS, then to the other CoreDNS hosts, which serve the same zones.
  #
  # The file is written here rather than through services.resolved.dnsDelegates,
  # whose rendering fails in the nixpkgs revision the Raspberry Pi platforms
  # pin (it looks for a Resolve section).
  environment.etc."systemd/dns-delegate.d/lanbat.dns-delegate" = lib.mkIf resolved {
    text = delegate;
  };
  systemd.services.systemd-resolved.reloadTriggers = lib.mkIf resolved [ delegate ];

  assertions =
    lib.mapAttrsToList (name: rs: {
      assertion = false;
      message =
        "lanbat: the DNS name ${name} is claimed more than once ("
        + lib.concatMapStringsSep ", " (r: "${r.from} → ${r.ip}") rs
        + "). Rename the host or drop the entry in deployment.dns.extraRecords.";
    }) clashes
    ++ [
      {
        assertion = short != domain;
        message = "lanbat: deployment.dns.shortSuffix must differ from deployment.domain.";
      }
    ];
}
