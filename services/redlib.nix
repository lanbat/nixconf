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
  pkgs,
  ...
}:

let
  domain = config.lanbat.deployment.domain;
  subdomain = "reddit";

  # nixpkgs carries an unreleased snapshot from 2025-12; Reddit keeps changing
  # how it treats clients, so run the newest release. Drop this override once
  # nixpkgs catches up.
  redlib = pkgs.redlib.overrideAttrs (old: rec {
    version = "0.40.0";
    src = pkgs.fetchFromGitHub {
      owner = "redlib-org";
      repo = "redlib";
      tag = "v${version}";
      hash = "sha256-qv7vIls9BwgwDQuKEQU72J10MpaMfcLvIGfzl628eNU=";
    };
    # 0.40 builds BoringSSL (to imitate a browser's TLS fingerprint) from the
    # btls-sys crate, which needs a C++ toolchain and runs git on its sources.
    nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
      pkgs.cmake
      pkgs.git
      pkgs.perl
      pkgs.rustPlatform.bindgenHook
    ];
    dontUseCmakeConfigure = true;
    # This test calls Reddit, which the build sandbox cannot reach; nixpkgs'
    # skip list names it as `oauth::test_...`, missing the `tests` module.
    checkFlags = (old.checkFlags or [ ]) ++ [ "--skip=oauth::tests::test_mobile_spoof_backend" ];
    cargoDeps = pkgs.rustPlatform.fetchCargoVendor {
      inherit src;
      hash = "sha256-XgHXqH4EF8TEOI+cKJ0Z2j6QYRCwGgv8bmY8gZpPN9c=";
    };
  });
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
    package = redlib;
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

  # Redlib creates a Reddit OAuth client on start-up and exits if it cannot
  # (Reddit has started answering the spoofed clients with 401). Keep it
  # retrying at a low rate rather than staying failed; it serves normally on
  # the runs where a request gets through.
  systemd.services.redlib.serviceConfig = {
    Restart = "on-failure";
    RestartSec = "5min";
  };
}
