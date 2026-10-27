# services/dashcast.nix
#
# dashcast — TECHO5's streamed-dashboard server. A headless Chrome beside Home
# Assistant opens the screen's dashboard at its size, sends only the parts that
# change as pictures, and replays touches. That is the only mode where a custom
# card and its CSS render as Home Assistant draws them; TECHO5's drawn mode
# knows only its own tiles.
#
# LAN only: the Echo Shows connect to :9555 directly (the device asks for the
# server with esphome.<node>_dashboard_server), so Caddy never proxies it and
# the firewall admits the devices' addresses alone. HA_TOKEN belongs to a
# non-administrator user; DASHCAST_KEY is shared with the device.
#
# Always-on: the screen's dashboard is the idle page.
{
  config,
  lib,
  ...
}:

let
  port = 9555;
  devices = config.lanbat.deployment.techo5Devices or { };
  enabled = devices != { };
in
{
  config = lib.mkMerge [
    (lib.mkIf enabled {
      lanbat.services.dashcast = {
        inherit port;
        tier = "always-on";
        units = [ "podman-dashcast" ];
        # root: the podman unit reads the environment file before it drops.
        secrets.dashcast-env.owner = "root";
      };

      virtualisation.oci-containers.containers.dashcast = {
        image = "ghcr.io/huskerminion/techo5-dashcast:latest";
        environment = {
          # Host networking: the loopback is Home Assistant, and the device
          # reaches the same port.
          HA_URL = "http://127.0.0.1:8123";
          LISTEN = ":${toString port}";
          # LANBAT's own pages, served by the assistant router on the loopback;
          # dashcast's browser (host networking) and the pages share that origin.
          # The screen shows Home by default and Results while a search is open.
          DASHCAST_PAGES = "Home=http://127.0.0.1:8092/screen,Results=http://127.0.0.1:8092/media";
        };
        environmentFiles = [ config.lanbat.secrets.dashcast-env.path ];
        extraOptions = [
          "--network=host"
          # Chrome wants more shared memory than the default.
          "--shm-size=256m"
        ];
      };

      # The policy module drops this port for every host; the Shows are not
      # hosts in that table, so admit each one by address. mkAfter lands the
      # ACCEPT above the generated DROP.
      networking.firewall.extraCommands = lib.mkAfter (
        lib.concatMapStrings (
          d: "iptables -I INPUT -p tcp --dport ${toString port} -s ${d.address} -j ACCEPT\n"
        ) (lib.attrValues devices)
      );
    })
  ];
}
