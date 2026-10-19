# tests/ha-oidc-login.nix
#
# Home Assistant logs in over OpenID Connect (auth_oidc) instead of Caddy
# forward auth plus hass-auth-header (docs/auth.md). Pure evaluation of the
# example server:
#
#   - auth_oidc is configured against Authentik's home-assistant application;
#   - no header provider remains: once Caddy stops stripping
#     X-Authentik-Username, one would accept a forged header as a login;
#   - Caddy proxies ha.<domain> without forward auth;
#   - the client secret is written into secrets.yaml before HA starts.
{
  lib,
  pkgs,
  inputs,
  self,
  agenix,
  disko,
  deploy-rs,
  nixpkgs,
  nixos-raspberrypi,
}:

let
  inputsWithSelf = inputs // {
    self = self // {
      lanbatPlugins = import ../plugins;
    };
  };
  exampleDeploy = import ../deployments/example/deploy.nix { inputs = inputsWithSelf; };
  lanbatLib = import ../lib {
    self = inputsWithSelf.self;
    inputs = inputsWithSelf;
    profiles = { };
    inherit
      nixpkgs
      nixos-raspberrypi
      agenix
      disko
      deploy-rs
      ;
  };
  config = (lanbatLib.mkProfile "example" exampleDeploy).configurations.example-server.config;
  domain = config.lanbat.deployment.domain;
  ha = config.services.home-assistant;
  oidc = ha.config.auth_oidc or { };
  components = map (p: p.pname or p.name or "") ha.customComponents;
  vhost = config.services.caddy.virtualHosts."ha.${domain}".extraConfig;
  preStart = lib.concatStringsSep "\n" (
    lib.toList (config.systemd.services.home-assistant.serviceConfig.ExecStartPre or [ ])
  );

  expect = name: cond: if cond then null else "FAIL: ${name}";
  cases = [
    (expect "auth_oidc uses the home-assistant client" (oidc.client_id or null == "home-assistant"))
    (expect "auth_oidc reads its secret from secrets.yaml" (
      oidc.client_secret or null == "!secret auth_oidc_client_secret"
    ))
    (expect "auth_oidc discovers Authentik's home-assistant application" (
      oidc.discovery_url or null
      == "https://auth.${domain}/application/o/home-assistant/.well-known/openid-configuration"
    ))
    (expect "auth_oidc trusts the internal Caddy CA" (
      (oidc.network or { }).tls_ca_path or null == "/var/lib/caddy-local-ca/ca-certificates.crt"
    ))
    (expect "auth_oidc redirects, links existing users and creates none" (
      (oidc.features or { }) == {
        default_redirect = true;
        require_existing_user = true;
        automatic_user_linking = true;
      }
    ))
    (expect "no header login is configured" (!(ha.config ? auth_header)))
    (expect "the auth_oidc component is installed" (lib.elem "christiaangoossens/auth_oidc" components))
    (expect "the auth_header component is gone" (
      !(lib.any (n: lib.hasSuffix "/auth_header" n) components)
    ))
    (expect "Caddy does not forward-auth Home Assistant" (
      !(lib.hasInfix "forward_auth" vhost) && !(lib.hasInfix "outpost.goauthentik.io" vhost)
    ))
    (expect "the client secret is written before Home Assistant starts" (
      lib.hasInfix "ha-oidc-secret" preStart
    ))
    (expect "the Home Assistant password login stays" (
      lib.any (p: p.type or null == "homeassistant") (
        ha.config.homeassistant.auth_providers or [
          { type = "homeassistant"; }
        ]
      )
    ))
  ];
  failures = lib.filter (x: x != null) cases;
in
pkgs.runCommand "ha-oidc-login-check" { } ''
  if [ ${toString (lib.length failures)} -ne 0 ]; then
    echo "ha-oidc-login checks failed:" >&2
    ${lib.concatStringsSep "\n" (map (msg: "echo \"  - ${msg}\" >&2") failures)}
    exit 1
  fi
  touch $out
''
