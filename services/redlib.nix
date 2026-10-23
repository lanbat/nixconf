# services/redlib.nix
#
# Redlib — privacy-respecting front-end for Reddit, the maintained fork of
# libreddit (archived upstream in 2023; nixpkgs no longer carries libreddit).
#
# Auth: intentionally NONE, like SearXNG.
#   Redlib is a stateless proxy: it keeps no accounts and no server-side data,
#   so it is open to anyone on the LAN and served without the Authentik
#   middleware.
#
# Always-on: yes.
#   Stateless — no database, no NFS, no secrets — so it starts at boot.
{
  config,
  ...
}:

let
  domain = config.lanbat.deployment.domain;
  subdomain = "reddit";
in
{
  lanbat.services.redlib = {
    inherit subdomain;
    port = 8081;
    auth = "none";
    dashboard = {
      group = "Utilities";
      name = "Redlib";
      description = "Reddit front-end";
    };
  };

  services.redlib = {
    enable = true;
    address = "127.0.0.1";
    port = config.lanbat.services.redlib.port;

    settings = {
      # Absolute URL: needed for the RSS feeds Redlib generates.
      REDLIB_FULL_URL = "https://${subdomain}.${domain}";
      REDLIB_ENABLE_RSS = true;
      # A LAN instance should stay out of search engines.
      REDLIB_ROBOTS_DISABLE_INDEXING = true;
    };
  };
}
