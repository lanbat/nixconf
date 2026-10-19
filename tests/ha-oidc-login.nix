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

  # auth_oidc rejects unknown keys and then never sets up, leaving only the
  # password login: validate against the packaged component's own schema.
  authOidc = pkgs.home-assistant.python.pkgs.callPackage ../pkgs/home-assistant-auth-oidc { };
  python = pkgs.home-assistant.python.withPackages (ps: [ ps.voluptuous ]);
  configJson = pkgs.writeText "auth_oidc.json" (builtins.toJSON { auth_oidc = oidc; });
  validate = pkgs.writeText "validate-auth-oidc.py" ''
    import importlib.util, json, pathlib, sys, types
    cfg = pathlib.Path(sys.argv[1]) / "custom_components/auth_oidc/config"
    pkg = types.ModuleType("cfg")
    pkg.__path__ = [str(cfg)]
    sys.modules["cfg"] = pkg
    for name in ("const", "schema"):
        spec = importlib.util.spec_from_file_location(f"cfg.{name}", cfg / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"cfg.{name}"] = mod
        spec.loader.exec_module(mod)
    sys.modules["cfg.schema"].CONFIG_SCHEMA(json.load(open(sys.argv[2])))
  '';

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
    (expect "auth_oidc redirects and links existing users by username" (
      (oidc.features or { }) == {
        default_redirect = true;
        automatic_user_linking = true;
      }
    ))
    # v1.2.1 creates an HA user for any new identity it is handed; only members
    # of the home-assistant group may get that far (Authentik binds the same
    # group to the application, tests/authentik-catalogue.nix).
    (expect "auth_oidc admits only the home-assistant group" (
      (oidc.roles or { }).user or null == "home-assistant"
    ))
    (expect "Authentik limits the HA application to the home-assistant group" (
      config.lanbat.services.home-assistant.access.groups == [ "home-assistant" ]
    ))
    (expect "no header login is configured" (!(ha.config ? auth_header)))
    (expect "the auth_oidc component is installed" (lib.elem "christiaangoossens/auth_oidc" components))
    (expect "the auth_header component is gone" (
      !(lib.any (n: lib.hasSuffix "/auth_header" n) components)
    ))
    (expect "Caddy does not forward-auth Home Assistant" (
      !(lib.hasInfix "forward_auth" vhost) && !(lib.hasInfix "outpost.goauthentik.io" vhost)
    ))
    (expect "the client secret is written before Home Assistant starts, as hass" (
      lib.hasInfix "ha-oidc-secret" preStart && lib.hasSuffix "/secrets.yaml hass" preStart
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
  ${python}/bin/python3 ${validate} ${authOidc} ${configJson} \
    || { echo "auth_oidc rejects the generated configuration" >&2; exit 1; }
  if [ ${toString (lib.length failures)} -ne 0 ]; then
    echo "ha-oidc-login checks failed:" >&2
    ${lib.concatStringsSep "\n" (map (msg: "echo \"  - ${msg}\" >&2") failures)}
    exit 1
  fi
  touch $out
''
