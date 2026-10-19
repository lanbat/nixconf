# tests/dns.nix
#
# VM test of the lanbat-dns plugin (modules/core/dns.nix).
#
# server runs CoreDNS from a hand-written service table: a web service
# (torrent) behind the server's Caddy, an endpoint-only service (mosquitto), a
# service on two hosts (voice-satellite, which gets no name), a second host
# that is not booted, and an extra record. client is the LAN: it queries, and
# it is the upstream resolver (a CoreDNS answering every name with 10.0.0.99).
#
# Asserts the short and full names, NXDOMAIN under the short suffix and
# fallthrough under the domain, forwarding, refusal outside the LAN subnet,
# the metrics page Telegraf reads, and the server resolving the profile's
# names through itself with systemd-resolved, as the real hosts run it, even
# when its interface has a DNS server of its own (as a router's IPv6
# advertisement gives it) that answers them wrongly.
#
# Run with: nix build -L .#checks.x86_64-linux.dns
{ pkgs }:

let
  hosts = {
    server = {
      role = "server";
      networking = {
        ip = "192.168.1.2";
        interface = "eth1";
        hostname = "server";
      };
    };
    pi = {
      role = "storage-pi";
      networking = {
        ip = "192.168.1.50";
        interface = "eth1";
        hostname = "pi";
      };
    };
  };

  # The profile-wide table lib/endpoints.nix would build, with only the fields
  # the DNS module reads.
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
    voice-satellite = {
      hosts = [
        "server"
        "pi"
      ];
      web = null;
    };
  };

in
pkgs.testers.runNixOSTest {
  name = "dns";

  nodes.client = {
    networking.interfaces.eth1.ipv4.addresses = [
      # Outside the LAN subnet the server admits (192.168.1.0/25). A /32, so
      # it is a source only when dig -b asks for it.
      {
        address = "192.168.1.200";
        prefixLength = 32;
      }
    ];
    networking.firewall.enable = false;

    # The upstream: every name is 10.0.0.99.
    services.coredns = {
      enable = true;
      config = ''
        .:53 {
          template IN A {
            answer "{{ .Name }} 60 IN A 10.0.0.99"
          }
        }
      '';
    };

    environment.systemPackages = [ pkgs.dig ];
  };

  nodes.server = {
    imports = [
      ../modules/core/settings.nix
      ../modules/core/host-context.nix
      ../modules/core/services.nix
      ../modules/core/overlay.nix
      ../modules/wiring/secrets.nix
      (import ../lib/overlay-providers.nix).none
      ./lib/age-option-stub.nix
      ../modules/core/lan-bridge.nix
      ../modules/core/dns.nix
    ];

    # As on the real hosts (networkd enables it there). Its global server is
    # only the gateway: the state a real host ends up in once resolved has
    # given up on CoreDNS (at boot, before CoreDNS listens) and stayed on the
    # gateway, made certain here instead of raced for.
    services.resolved.enable = true;
    networking.nameservers = pkgs.lib.mkForce [ "192.168.1.1" ];

    lanbat.profile = "test";
    lanbat.hostKey = "server";
    lanbat.hosts = hosts;
    lanbat.endpoints = endpoints;
    lanbat.deployment = {
      domain = "home.test";
      gatewayIp = "192.168.1.1";
      lanSubnet = "192.168.1.0/25";
      secrets = {
        provider = "none";
        root = ../secrets;
      };
      dns = {
        shortSuffix = "lan";
        upstreams = [ "192.168.1.1" ];
        extraRecords.router = "192.168.1.1";
      };
    };

    environment.systemPackages = [
      pkgs.dig
      pkgs.curl
    ];
  };

  testScript = ''
    def answer(name, src=""):
        return client.succeed(f"dig +short {src} @192.168.1.2 {name} A").strip()

    def status(name, src=""):
        out = client.succeed(f"dig {src} @192.168.1.2 {name} A")
        return out.split("status: ")[1].split(",")[0]

    start_all()
    client.wait_for_unit("coredns.service")
    server.wait_for_unit("coredns.service")
    client.wait_until_succeeds("dig +short @192.168.1.2 server.lan | grep -q 192.168.1.2", timeout=60)

    with subtest("web names point at the Caddy host, under both suffixes"):
        assert answer("torrent.lan") == "192.168.1.2"
        assert answer("torrent.home.test") == "192.168.1.2"
        assert answer("ca.lan") == "192.168.1.2"

    with subtest("services without a subdomain, hosts and extra records"):
        assert answer("mosquitto.lan") == "192.168.1.2"
        assert answer("pi.lan") == "192.168.1.50"
        assert answer("pi.home.test") == "192.168.1.50"
        assert answer("router.lan") == "192.168.1.1"

    with subtest("an unknown short name is NXDOMAIN, a service on two hosts has none"):
        assert status("nope.lan") == "NXDOMAIN"
        assert status("voice-satellite.lan") == "NXDOMAIN"

    with subtest("other names, and unknown names under the domain, go upstream"):
        assert answer("example.org") == "10.0.0.99"
        assert answer("nope.home.test") == "10.0.0.99"

    with subtest("the server resolves the profile's names through its own CoreDNS"):
        # The interface's own DNS server is the client, which answers every
        # name with 10.0.0.99, like a router that does not know the names.
        server.succeed("resolvectl dns eth1 192.168.1.1")
        server.succeed("resolvectl flush-caches")
        server.succeed("getent hosts torrent.lan | grep -q '^192.168.1.2 '")
        server.succeed("getent hosts torrent.home.test | grep -q '^192.168.1.2 '")
        server.succeed("getent hosts pi.lan | grep -q '^192.168.1.50 '")
        # Other names still resolve.
        server.succeed("getent hosts example.org | grep -q '^10.0.0.99 '")

    with subtest("queries from outside the LAN subnet are refused"):
        assert status("refused.lan", "-b 192.168.1.200") == "REFUSED"

    with subtest("metrics are exported for Telegraf"):
        server.succeed("curl -sf -o /tmp/metrics http://127.0.0.1:9153/metrics")
        server.succeed("grep -q coredns_dns_requests_total /tmp/metrics")
  '';
}
