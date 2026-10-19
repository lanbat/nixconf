# Home Assistant OpenID Connect Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Caddy forward auth and `hass-auth-header` on Home Assistant with the
`auth_oidc` component, so browsers and the Android companion app both log in through
Authentik on `ha.<domain>`.

**Architecture:** Home Assistant becomes an `auth = "app"` service whose own login page
redirects to Authentik over OpenID Connect (`auth_oidc` v1.2.1, packaged under `pkgs/`).
The existing confidential Authentik client is reused. A root `ExecStartPre` step copies
its secret into Home Assistant's `secrets.yaml`, changing only its own key. The
generated Authentik blueprint drops the forward-auth proxy by itself.

**Tech Stack:** NixOS modules, Nix (`buildHomeAssistantComponent`, `writeShellApplication`,
`runCommand` checks), Home Assistant 2026.x, Authentik blueprints, Caddy.

**Spec:** `docs/superpowers/specs/2026-10-19-ha-oidc-login-design.md`

## Global Constraints

- The repository is public: never write the real domain, IP addresses, user IDs or secret
  values into a tracked file. Use `config.lanbat.deployment.domain` in Nix, and
  `<domain>` in prose.
- `auth_oidc` version `1.2.1` exactly: tag `v1.2.1`, source hash
  `sha256-vwQDrMM4phbrXT85Syyz6hWEIhLB3TKNNTM04OdvNWk=`, npm deps hash
  `sha256-yAm08NVz2Ln6ob9LoPfxuz6U1oBS4AEydIQHhv0WpOg=`.
- OIDC client ID `home-assistant`; secret variable `AUTHENTIK_HA_CLIENT_SECRET` in the
  `authentik-oidc-secrets` file; redirect path `/auth/oidc/callback`.
- `secrets.yaml` key `auth_oidc_client_secret`. Every other key already in the file (the
  live server has `mqtt_password`) is preserved.
- `features`: `default_redirect: true`, `automatic_user_linking: true` (switched off in
  Task 5); `roles.user: home-assistant`, and `access.groups = [ "home-assistant" ]` on
  the service. (Corrected after the final review: v1.2.1 has no `require_existing_user`;
  the Task 3 code below shows the original, superseded version.)
- `network.tls_ca_path: /var/lib/caddy-local-ca/ca-certificates.crt`.
- The `homeassistant` auth provider, the owner's password, `ip_ban_enabled = true` and
  `login_attempts_threshold = 5` stay. No TOTP on the owner account.
- Follow CONTRIBUTING.md: `nix fmt`, and every commit message ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The working tree has unrelated, uncommitted changes (`modules/wiring/workload-gate.nix`,
  `services/nextcloud.nix`, three untracked `secrets/*.age`). Never stage them: stage
  files by name, never `git add -A` or `git commit -a`.

## Review Focus

1. **A `secrets.yaml` that already holds other keys** (the live server has
   `mqtt_password`): they must survive every start. Pinned in Task 2's script test.
2. **Restarts**: running the writer twice must not duplicate the key or leave a stale
   value. Pinned in Task 2's script test.
3. **A forged `X-Authentik-Username` header** once Caddy stops stripping it: no header
   provider may remain. Pinned in Task 3's evaluation test (no `auth_header` in the
   configuration or the components) and checked live in Task 4.
4. **The client secret missing from `authentik-oidc-secrets`**: Home Assistant must not
   start with an empty secret, and the existing `secrets.yaml` must be left untouched.
   Pinned in Task 2's script test.
5. **A secret containing YAML-special characters** (`:`, `#`, quotes): the written value
   must parse back exactly. Pinned in Task 2's script test with a value containing them.

---

## File structure

| File | Change | Responsibility |
|---|---|---|
| `pkgs/home-assistant-auth-oidc/default.nix` | create | The `auth_oidc` v1.2.1 component |
| `pkgs/home-assistant-oidc-secret/default.nix` | create | `ha-oidc-secret`: puts the client secret into `secrets.yaml`, keeping other keys |
| `tests/ha-oidc-secret.nix` | create | Script behaviour: merge, idempotence, missing secret, special characters |
| `tests/ha-oidc-login.nix` | create | Evaluation of the example server: HA config, components, Caddy vhost, unit |
| `flake.nix` | modify | Register both checks |
| `tests/authentik-catalogue.nix` | modify | HA has no proxy provider any more; new redirect URI; app no longer hidden |
| `services/home-assistant.nix` | modify | `auth = "app"`, `oidc`, `readsSecrets`, components, `auth_oidc` config, `ExecStartPre`, comments |
| `pkgs/home-assistant-auth-header/` | delete | No longer used |
| `pkgs/home-assistant-bootstrap/bootstrap-ha.sh` | modify | Comment only ("header auth" → OIDC linking) |
| `docs/auth.md`, `docs/architecture.md` | modify | HA row, login description, adding a person |

---

### Task 1: Package `auth_oidc` v1.2.1

**Files:**
- Create: `pkgs/home-assistant-auth-oidc/default.nix`

**Interfaces:**
- Consumes: nothing.
- Produces: a package called with
  `pkgs.home-assistant.python.pkgs.callPackage ../pkgs/home-assistant-auth-oidc { }`.
  It is a `buildHomeAssistantComponent` with `pname = "christiaangoossens/auth_oidc"` and
  `version = "1.2.1"`, installing `custom_components/auth_oidc`.

- [ ] **Step 1: Write the build check and watch it fail**

```bash
cd /home/traph/projects/lanbat/nixos
nix build --impure --no-link --print-out-paths --expr '
  let p = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.x86_64-linux;
  in p.home-assistant.python.pkgs.callPackage ./pkgs/home-assistant-auth-oidc { }'
```

Expected: FAIL, because `pkgs/home-assistant-auth-oidc` does not exist.

- [ ] **Step 2: Create the package**

`pkgs/home-assistant-auth-oidc/default.nix`:

```nix
# pkgs/home-assistant-auth-oidc/default.nix
#
# auth_oidc (hass-oidc-auth): OpenID Connect login for Home Assistant. nixpkgs
# ships 0.6.5-alpha, older than v1.2.0's fix for an open redirect on
# /auth/welcome (GHSA-gg9c-6c2r-6x28) and its mobile-login fixes, so this
# follows the nixpkgs package at v1.2.1. Called from Home Assistant's own
# Python package set (services/home-assistant.nix), which provides
# aiofiles, jinja2 and joserfc.
{
  lib,
  buildHomeAssistantComponent,
  fetchFromGitHub,
  fetchNpmDeps,
  aiofiles,
  jinja2,
  joserfc,
  nodejs,
  npmHooks,
}:

buildHomeAssistantComponent rec {
  owner = "christiaangoossens";
  domain = "auth_oidc";
  version = "1.2.1";

  src = fetchFromGitHub {
    owner = "christiaangoossens";
    repo = "hass-oidc-auth";
    tag = "v${version}";
    hash = "sha256-vwQDrMM4phbrXT85Syyz6hWEIhLB3TKNNTM04OdvNWk=";
  };

  # The welcome and device-code pages' stylesheet is built with Tailwind.
  env.npmDeps = fetchNpmDeps {
    name = "${domain}-npm-deps";
    inherit src;
    hash = "sha256-yAm08NVz2Ln6ob9LoPfxuz6U1oBS4AEydIQHhv0WpOg=";
  };

  nativeBuildInputs = [
    npmHooks.npmConfigHook
    nodejs
  ];

  dependencies = [
    aiofiles
    jinja2
    joserfc
  ];

  postBuild = ''
    npm run css
  '';

  # Upstream's tests need a running Home Assistant test harness.
  doCheck = false;

  meta = {
    changelog = "https://github.com/christiaangoossens/hass-oidc-auth/releases/tag/v${version}";
    description = "OpenID Connect authentication provider for Home Assistant";
    homepage = "https://github.com/christiaangoossens/hass-oidc-auth";
    license = lib.licenses.mit;
  };
}
```

- [ ] **Step 3: Build it and check the output**

```bash
cd /home/traph/projects/lanbat/nixos
out=$(nix build --impure --no-link --print-out-paths --expr '
  let p = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.x86_64-linux;
  in p.home-assistant.python.pkgs.callPackage ./pkgs/home-assistant-auth-oidc { }')
jq -r .version "$out"/custom_components/auth_oidc/manifest.json
test -s "$out"/custom_components/auth_oidc/static/style.css && echo "style.css built"
```

Expected: the build succeeds, then prints `1.2.1` and `style.css built`.

If the build fails at `npm run css` because the generated `style.css` lands somewhere
else, find it with `find "$out" -name style.css`. Upstream's `package.json` writes it to
`./custom_components/auth_oidc/static/style.css`, and that must be the path.

- [ ] **Step 4: Format and commit**

```bash
cd /home/traph/projects/lanbat/nixos
nix fmt -- pkgs/home-assistant-auth-oidc/default.nix
git add pkgs/home-assistant-auth-oidc/default.nix
git commit -m "Package auth_oidc 1.2.1 for Home Assistant

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `ha-oidc-secret`, the `secrets.yaml` writer

**Files:**
- Create: `pkgs/home-assistant-oidc-secret/default.nix`
- Create: `tests/ha-oidc-secret.nix`
- Modify: `flake.nix` (the `checks` set, next to `ha-dashboards`)

**Interfaces:**
- Consumes: nothing.
- Produces: `pkgs.callPackage ../pkgs/home-assistant-oidc-secret { }`, a
  `writeShellApplication` named `ha-oidc-secret` with usage
  `ha-oidc-secret <env-file> <secrets.yaml> <owner:group>`. It reads
  `AUTHENTIK_HA_CLIENT_SECRET` from the env file and sets the key
  `auth_oidc_client_secret` in the YAML file to a JSON-quoted string. It keeps every
  other line, writes atomically with mode 0600 and the given owner, and exits 1 without
  touching the YAML file when the variable is empty or missing.

- [ ] **Step 1: Write the failing test**

`tests/ha-oidc-secret.nix`:

```nix
# tests/ha-oidc-secret.nix
#
# ha-oidc-secret (pkgs/home-assistant-oidc-secret) puts Home Assistant's
# OIDC client secret into secrets.yaml before HA starts. The file holds other
# keys too (the live server has mqtt_password), so the writer must change
# only its own key, stay idempotent across restarts, refuse an empty secret
# without touching the file, and quote values YAML would misread.
{ pkgs }:

let
  writer = pkgs.callPackage ../pkgs/home-assistant-oidc-secret { };
  yq = "${pkgs.yq-go}/bin/yq";
in
pkgs.runCommand "ha-oidc-secret-check" { } ''
  set -euo pipefail
  owner="$(id -un):$(id -gn)"
  cd "$(mktemp -d)"

  # A secret with characters YAML would misread unquoted.
  cat > env <<'EOF'
  AUTHENTIK_HA_CLIENT_SECRET='a:b #c "d" e'
  AUTHENTIK_OTHER_CLIENT_SECRET=other
  EOF
  printf 'mqtt_password: keep-me\n' > secrets.yaml

  # 1. Sets the key and keeps the other one.
  ${writer}/bin/ha-oidc-secret env secrets.yaml "$owner"
  [ "$(${yq} '.auth_oidc_client_secret' secrets.yaml)" = 'a:b #c "d" e' ]
  [ "$(${yq} '.mqtt_password' secrets.yaml)" = keep-me ]
  [ "$(stat -c %a secrets.yaml)" = 600 ]

  # 2. Idempotent: a second run leaves one key with the new value.
  sed -i "s/^AUTHENTIK_HA_CLIENT_SECRET=.*/AUTHENTIK_HA_CLIENT_SECRET=rotated/" env
  ${writer}/bin/ha-oidc-secret env secrets.yaml "$owner"
  [ "$(grep -c '^auth_oidc_client_secret:' secrets.yaml)" = 1 ]
  [ "$(${yq} '.auth_oidc_client_secret' secrets.yaml)" = rotated ]
  [ "$(${yq} '.mqtt_password' secrets.yaml)" = keep-me ]

  # 3. A missing secret fails and leaves the file as it was.
  cp secrets.yaml before.yaml
  printf 'AUTHENTIK_OTHER_CLIENT_SECRET=other\n' > env
  if ${writer}/bin/ha-oidc-secret env secrets.yaml "$owner" 2>err; then
    echo "expected failure on a missing secret" >&2; exit 1
  fi
  grep -q AUTHENTIK_HA_CLIENT_SECRET err
  cmp before.yaml secrets.yaml

  # 4. No secrets.yaml yet: it is created.
  rm secrets.yaml
  printf 'AUTHENTIK_HA_CLIENT_SECRET=fresh\n' > env
  ${writer}/bin/ha-oidc-secret env secrets.yaml "$owner"
  [ "$(${yq} '.auth_oidc_client_secret' secrets.yaml)" = fresh ]

  touch $out
''
```

Register it in `flake.nix`, directly after the `ha-dashboards` line:

```nix
        ha-oidc-secret = import ./tests/ha-oidc-secret.nix { inherit pkgs; };
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /home/traph/projects/lanbat/nixos
nix build -L path:.#checks.x86_64-linux.ha-oidc-secret
```

Expected: FAIL, because `pkgs/home-assistant-oidc-secret` does not exist.

- [ ] **Step 3: Write the script**

`pkgs/home-assistant-oidc-secret/default.nix`:

```nix
# pkgs/home-assistant-oidc-secret/default.nix
#
# ha-oidc-secret <env-file> <secrets.yaml> <owner:group>
#
# Home Assistant's YAML cannot read environment variables, so auth_oidc's
# client secret reaches it as !secret auth_oidc_client_secret. This copies
# AUTHENTIK_HA_CLIENT_SECRET from the Authentik client-secret file into
# secrets.yaml, changing only that key: the file is HA's own and may hold
# others. The value is written as a JSON string, which YAML reads exactly.
# A missing secret fails before anything is written, so HA never starts with
# an empty one.
{ writeShellApplication, jq }:

writeShellApplication {
  name = "ha-oidc-secret";
  runtimeInputs = [ jq ];
  text = ''
    env_file=$1
    yaml=$2
    owner=$3

    # The file is KEY=value lines, as the other readers source it.
    secret=$(
      set -a
      # shellcheck disable=SC1090
      . "$env_file"
      printf '%s' "''${AUTHENTIK_HA_CLIENT_SECRET:-}"
    )
    if [ -z "$secret" ]; then
      echo "ha-oidc-secret: AUTHENTIK_HA_CLIENT_SECRET is missing from $env_file" >&2
      exit 1
    fi

    tmp=$(mktemp "$yaml.XXXXXX")
    trap 'rm -f "$tmp"' EXIT
    chmod 0600 "$tmp"
    if [ -e "$yaml" ]; then
      grep -v '^auth_oidc_client_secret:' "$yaml" > "$tmp" || true
    fi
    printf 'auth_oidc_client_secret: %s\n' "$(jq -cn --arg s "$secret" '$s')" >> "$tmp"
    chown "$owner" "$tmp"
    mv -f "$tmp" "$yaml"
    trap - EXIT
  '';
}
```

- [ ] **Step 4: Run the test and watch it pass**

```bash
cd /home/traph/projects/lanbat/nixos
nix build -L path:.#checks.x86_64-linux.ha-oidc-secret
```

Expected: PASS (the build succeeds with no output from the checks).

- [ ] **Step 5: Format and commit**

```bash
cd /home/traph/projects/lanbat/nixos
nix fmt -- pkgs/home-assistant-oidc-secret/default.nix tests/ha-oidc-secret.nix flake.nix
git add pkgs/home-assistant-oidc-secret/default.nix tests/ha-oidc-secret.nix flake.nix
git commit -m "ha-oidc-secret: put the OIDC client secret into Home Assistant's secrets.yaml

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Switch Home Assistant to the OIDC login

**Files:**
- Create: `tests/ha-oidc-login.nix`
- Modify: `flake.nix` (register `ha-oidc-login` next to `authentik-catalogue`)
- Modify: `tests/authentik-catalogue.nix` (the `exampleIdentity` expectations, about
  lines 213–300, plus one new case)
- Modify: `services/home-assistant.nix` (lines 25–37 header comment, 83 `let`, 1364–1371
  `ssoUsers`, 1428–1440 service description, 1491–1504 unit, 1640–1650
  `customComponents`, 1784–1787 `auth_header`)
- Delete: `pkgs/home-assistant-auth-header/default.nix`
- Modify: `pkgs/home-assistant-bootstrap/bootstrap-ha.sh` (lines 9 and 98, comments)
- Modify: `docs/auth.md`, `docs/architecture.md`

**Interfaces:**
- Consumes: Task 1's `pkgs/home-assistant-auth-oidc`; Task 2's
  `pkgs/home-assistant-oidc-secret` (`ha-oidc-secret <env-file> <secrets.yaml> <owner:group>`).
- Produces: the example server's `services.home-assistant.config.auth_oidc`, with no
  `auth_header`, and a Caddy vhost for `ha.<domain>` with no forward auth.

- [ ] **Step 1: Write the failing evaluation test**

`tests/ha-oidc-login.nix`:

```nix
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
      lib.any (p: p.type or null == "homeassistant") (ha.config.homeassistant.auth_providers or [
        { type = "homeassistant"; }
      ])
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
```

Register it in `flake.nix`, directly after the `authentik-catalogue = import ...;`
block, with the same argument list:

```nix
        ha-oidc-login = import ./tests/ha-oidc-login.nix {
          inherit
            lib
            pkgs
            inputs
            self
            agenix
            disko
            deploy-rs
            nixpkgs
            nixos-raspberrypi
            ;
        };
```

- [ ] **Step 2: Update the catalogue test's expectations**

In `tests/authentik-catalogue.nix`, Home Assistant no longer has a proxy provider, and
its OIDC application is no longer hidden (only services with both a proxy and OIDC are).

Replace

```nix
  exampleProxyIds = map (n: "provider-${n}") proxied ++ [
    "provider-home-assistant-proxy"
    "provider-immich-proxy"
  ];
```

with

```nix
  exampleProxyIds = map (n: "provider-${n}") proxied ++ [
    "provider-immich-proxy"
  ];
```

In `proxyProviders`, delete the element

```nix
        {
          id = "provider-home-assistant-proxy";
          name = "Home Assistant (proxy)";
        }
```

In `proxyApps`, delete the element

```nix
        {
          slug = "home-assistant-proxy";
          provider = "provider-home-assistant-proxy";
        }
```

In `oidcApps`, replace

```nix
          hidden = n == "home-assistant" || n == "immich";
```

with

```nix
          hidden = n == "immich";
```

Add this case to `cases`, next to the RomM redirect case:

```nix
    (expect "Home Assistant's OIDC redirect URI is auth_oidc's callback" (
      let
        p = byId "provider-home-assistant" (oidcProviders base);
      in
      p != null
      && map (u: u.url) p.attrs.redirect_uris == [
        "https://ha.${exampleDeploy.deployment.domain}/auth/oidc/callback"
      ]
    ))
```

- [ ] **Step 3: Run both tests and watch them fail**

```bash
cd /home/traph/projects/lanbat/nixos
nix build -L path:.#checks.x86_64-linux.ha-oidc-login
nix build -L path:.#checks.x86_64-linux.authentik-catalogue
```

Expected: both FAIL. `ha-oidc-login` lists every case except "the Home Assistant password
login stays". `authentik-catalogue` fails on the pinned identities and the new redirect
case.

- [ ] **Step 4: Change `services/home-assistant.nix`**

4a. In the `let` block, replace line 83

```nix
  authHeaderComponent = pkgs.callPackage ../pkgs/home-assistant-auth-header { };
```

with

```nix
  # Called from HA's own Python package set, which provides its dependencies,
  # as nixpkgs calls home-assistant-custom-components.
  authOidcComponent = pkgs.home-assistant.python.pkgs.callPackage ../pkgs/home-assistant-auth-oidc { };
  oidcSecret = pkgs.callPackage ../pkgs/home-assistant-oidc-secret { };
```

4b. In the service description (about line 1430), replace

```nix
      auth = "forward-auth";
      apiClients = true; # companion apps — /auth/token and /api/* bypass forward-auth
      # Sign-in is forward auth plus hass-auth-header; the OIDC client stays
      # for optional native OAuth integrations.
      oidc = {
        redirectPaths = [ "/auth/external/callback" ];
        secretVariable = "AUTHENTIK_HA_CLIENT_SECRET";
      };
```

with

```nix
      # Sign-in is HA's own login, which hands over to Authentik over OpenID
      # Connect (auth_oidc). Forward auth would break the companion apps: they
      # start the login in a WebView and finish it in the browser, which never
      # has the outpost's session cookie (docs/auth.md).
      auth = "app";
      apiClients = true; # companion apps and REST clients call HA directly
      oidc = {
        redirectPaths = [ "/auth/oidc/callback" ];
        secretVariable = "AUTHENTIK_HA_CLIENT_SECRET";
      };
```

In the same description, extend `readsSecrets` (about line 1426). Replace

```nix
      readsSecrets = lib.optional (config.lanbat.hasService "mosquitto") "mosquitto-ha-pass";
```

with

```nix
      readsSecrets =
        lib.optional (config.lanbat.hasService "mosquitto") "mosquitto-ha-pass"
        # auth_oidc's client secret, copied into secrets.yaml before HA starts.
        ++ [ "authentik-oidc-secrets" ];
```

4c. In `systemd.services.home-assistant` (about line 1491), add after the `wants` line:

```nix
      # auth_oidc reads its client secret as !secret auth_oidc_client_secret.
      # Root ("+"): the Authentik client-secret file is readable by root alone.
      serviceConfig.ExecStartPre = [
        "+${oidcSecret}/bin/ha-oidc-secret ${config.lanbat.secrets.authentik-oidc-secrets.path} ${config.services.home-assistant.configDir}/secrets.yaml hass:hass"
      ];
```

4d. In `customComponents` (about line 1645), replace

```nix
        (authHeaderComponent.overridePythonAttrs (_: {
          doCheck = false;
        }))
```

with

```nix
        authOidcComponent
```

4e. Replace the `auth_header` block (about line 1784)

```nix
        # Authentik forward-auth → header-based login (users must exist in HA).
        auth_header = {
          username_header = "X-Authentik-Username";
        };
```

with

```nix
        # Login through Authentik over OpenID Connect. Users must already exist
        # in HA (the bootstrap creates ssoUsers): an Authentik username links to
        # the HA user of the same name. Linking stays on only until each of them
        # has logged in once (docs/auth.md). HA's password login remains at
        # ?skip_oidc_redirect=true.
        auth_oidc = {
          client_id = "home-assistant";
          client_secret = "!secret auth_oidc_client_secret";
          discovery_url = "https://auth.${domain}/application/o/home-assistant/.well-known/openid-configuration";
          display_name = "Authentik";
          features = {
            default_redirect = true;
            require_existing_user = true;
            automatic_user_linking = true;
          };
          # Authentik is served with the internal Caddy CA.
          network.tls_ca_path = "/var/lib/caddy-local-ca/ca-certificates.crt";
        };
```

If `domain` is not already bound in the file's `let` block, add
`domain = config.lanbat.deployment.domain;` there, which is the pattern CONTRIBUTING
gives.

4f. Rewrite the header comment's "Auth with Authentik" section (lines 25–37) as:

```nix
# Auth with Authentik
# -------------------
# HA's own login page hands over to Authentik over OpenID Connect (the
# auth_oidc custom component); Caddy proxies HA without forward auth. Browsers
# and the companion apps use the same hostname: the apps sign in with a device
# code that is entered in the phone's browser. An Authentik username links to
# the existing HA user of the same name, which the bootstrap provisions from
# ssoUsers; no new HA users are created.
#
# API clients authenticate with HA long-lived tokens as usual. HA's password
# login stays, at ?skip_oidc_redirect=true, for the owner and the loopback
# setup scripts.
#
# First-run onboarding is completed automatically by home-assistant-bootstrap
# (owner account + SSO user mirror).
```

4g. Replace the `ssoUsers` description (lines 1367–1370):

```nix
      description = ''
        Authentik usernames to provision as Home Assistant users. Each logs in
        through Authentik (auth_oidc), which links the Authentik username to
        the HA user of the same name; usernames must match Authentik exactly.
      '';
```

- [ ] **Step 5: Remove the header package and fix the bootstrap comments**

```bash
cd /home/traph/projects/lanbat/nixos
git rm -q pkgs/home-assistant-auth-header/default.nix
grep -rn 'home-assistant-auth-header\|authHeaderComponent' --include='*.nix' . | grep -v '^./result'
```

Expected: no output.

In `pkgs/home-assistant-bootstrap/bootstrap-ha.sh`, replace line 9

```bash
# Space-separated Authentik usernames to mirror in HA (header auth only).
```

with

```bash
# Space-separated Authentik usernames to mirror in HA (they log in through
# Authentik; auth_oidc links each to the HA user of the same name).
```

and line 98

```bash
    # Random password — login is via Authentik header auth, not this password.
```

with

```bash
    # Random password: login is through Authentik (auth_oidc), not this password.
```

- [ ] **Step 6: Run the tests and watch them pass**

```bash
cd /home/traph/projects/lanbat/nixos
nix build -L path:.#checks.x86_64-linux.ha-oidc-login
nix build -L path:.#checks.x86_64-linux.authentik-catalogue
nix build -L path:.#checks.x86_64-linux.ha-oidc-secret
nix eval --raw path:.#nixosConfigurations.example-server.config.system.build.toplevel.drvPath
for c in assertions plugins settings-guard validate-deploy; do nix build -L "path:.#checks.x86_64-linux.$c" || echo "FAILED: $c"; done
```

Expected: every build succeeds, the eval prints a `.drv` path, and no `FAILED:` line
appears.

If the `assertions` check rejects `apiClients = true` with `auth = "app"`, read the
assertion's message in `modules/wiring/checks.nix`. CONTRIBUTING lists that combination
for Jellyfin and Vaultwarden, so it is expected to pass.

- [ ] **Step 7: Update the docs**

`docs/auth.md`, line 94, replace the Home Assistant row with:

```markdown
| Home Assistant | app | yes | OIDC login (`auth_oidc`, see below); companion apps sign in with a device code |
```

`docs/auth.md`, replace the "Header login (Home Assistant)" bullet (lines 179–185) with:

```markdown
- **OIDC login linked by username (Home Assistant).** HA's own login page hands over to
  authentik over OpenID Connect (the `auth_oidc` component, `services/home-assistant.nix`).
  The `preferred_username` claim links to the HA user of the same name, which the
  `home-assistant-bootstrap` oneshot provisions from `lanbat.homeAssistant.ssoUsers`
  (default `["akadmin"]`); `require_existing_user` stops it creating new HA users.
  Entitlement inside HA is HA's own (owner/admin). The companion apps use the same
  hostname and sign in with a device code entered in the phone's browser. Caddy does not
  forward-auth HA: the apps start the login in a WebView and finish it in the browser,
  which never has the outpost's session cookie. HA's password login stays at
  `?skip_oidc_redirect=true`.

  **Adding a person:** add their authentik username to `lanbat.homeAssistant.ssoUsers`,
  set `automatic_user_linking = true` in `services/home-assistant.nix`, deploy, have
  them log in once through authentik, then set it back to `false` and deploy again.
  While linking is on, any authentik account whose username matches an HA user takes
  that user over.

  **After switching from forward auth:** authentik keeps objects that drop out of the
  blueprint, so delete the old "Home Assistant (proxy)" provider and application in the
  authentik UI (Applications → Providers, and Applications → Applications).
```

`docs/architecture.md`, line 120, replace the Home Assistant row with:

```markdown
| Home Assistant | Native OIDC (`auth_oidc`, linked by username) + local password fallback | Companion apps sign in through authentik with a device code; API clients use long-lived tokens |
```

- [ ] **Step 8: Format and commit**

```bash
cd /home/traph/projects/lanbat/nixos
nix fmt -- services/home-assistant.nix tests/ha-oidc-login.nix tests/authentik-catalogue.nix flake.nix
git add services/home-assistant.nix tests/ha-oidc-login.nix tests/authentik-catalogue.nix flake.nix \
  pkgs/home-assistant-bootstrap/bootstrap-ha.sh docs/auth.md docs/architecture.md
git status --short   # pkgs/home-assistant-auth-header is staged as deleted; nothing else unrelated is staged
git commit -m "Home Assistant: log in through Authentik over OpenID Connect

Forward auth broke the companion apps: they start the login in a WebView
and finish it in the browser, which never has the outpost's session
cookie. HA now uses auth_oidc on its own login page, linked to existing
users by username, and Caddy proxies it without forward auth. The header
login is removed: without forward auth, Caddy no longer strips the header.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Deploy and verify on the server (operator task)

`<server>` and `<domain>` stand for the homelab server's address and domain from the
gitignored `deployments/homelab/deploy.nix`; they are not written into this tracked
file. This task changes the live system. Each step is run and its result reported. Stop and
report at the first failure; do not improvise fixes on the server.

**Files:** none.

- [ ] **Step 1: Pre-flight on the server**

```bash
ssh admin@<server> 'f=$(sudo find /run/agenix* -name "authentik-oidc-secrets*" | head -1); sudo grep -c "^AUTHENTIK_HA_CLIENT_SECRET=" "$f"'
ssh admin@<server> 'sudo cp -a /var/lib/hass/secrets.yaml /var/lib/hass/secrets.yaml.pre-oidc; sudo cp -a /var/lib/hass/.storage/auth /var/lib/hass/.storage/auth.pre-oidc'
```

Expected: `1`, then the two backups exist.

- [ ] **Step 2: Deploy without the unrelated working-tree changes**

`deploy path:.` deploys the working tree, including uncommitted files. Stash the
unrelated changes first, and ask the user before stashing them:

```bash
cd /home/traph/projects/lanbat/nixos
git stash push -m "unrelated WIP" -- modules/wiring/workload-gate.nix services/nextcloud.nix
nix develop -c deploy --skip-checks path:.#homelab-server
git stash pop
```

Expected: the deploy succeeds, and `home-assistant.service` is active afterwards
(`ssh admin@<server> systemctl is-active home-assistant`).

- [ ] **Step 2b: Add `akadmin` to the `home-assistant` group**

The blueprint has created the group. In the Authentik admin UI (Directory → Groups →
home-assistant → Users), add `akadmin` and everyone else who used Home Assistant. Until
then, Authentik refuses their login to the application.

- [ ] **Step 3: Server-side checks**

```bash
ssh admin@<server> 'sudo sed -E "s/:.*/: <hidden>/" /var/lib/hass/secrets.yaml'
curl -sk https://ha.<domain>/auth/providers | jq -r '.providers[].type'
curl -sk -o /dev/null -w '%{http_code}\n' https://ha.<domain>/
```

Expected: the keys `mqtt_password` and `auth_oidc_client_secret`; the provider types
include `auth_oidc` and `homeassistant` but not `auth_header`; the frontend returns
`200` rather than a `302` to `auth.<domain>`.

- [ ] **Step 4: Forged header is ignored**

```bash
curl -sk https://ha.<domain>/auth/providers -H 'X-Authentik-Username: akadmin' | jq -r '.providers[].type'
curl -sk -X POST https://ha.<domain>/auth/login_flow -H 'X-Authentik-Username: akadmin' \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"https://ha.<domain>/","handler":["homeassistant",null],"redirect_uri":"https://ha.<domain>/"}' | jq '{type, step_id}'
```

Expected: no `auth_header` provider; the login flow answers with `step_id: "init"`
(a username and password are asked for), not a `create_entry` result.

- [ ] **Step 5: Browser login (the user)**

The user opens `https://ha.<domain>` in a private window and logs in through
Authentik. Then:

```bash
ssh admin@<server> 'sudo jq -r ".data.users[] | select(.name==\"akadmin\") | [.id[0:8], .is_owner, (.group_ids|join(\",\"))] | @tsv" /var/lib/hass/.storage/auth; sudo jq -r ".data.credentials[] | [.auth_provider_type, .user_id[0:8]] | @tsv" /var/lib/hass/.storage/auth'
```

Expected: `akadmin` keeps the same user ID prefix as before, `is_owner` is `true`, and
the groups are `system-admin`. There is now an `auth_oidc` credential on that same user
ID. The user confirms their dashboards are there.

- [ ] **Step 6: Fallback and loopback clients**

The user opens `https://ha.<domain>/?skip_oidc_redirect=true` and sees the password
login. Then rerun the units that log in with the owner's password. The Homepage token
script (`pkgs/homepage-config`'s `createHaToken`) is defined but not run by any unit, so
it is not part of this check.

```bash
ssh admin@<server> 'sudo systemctl restart home-assistant-bootstrap.service music-assistant-setup.service; sleep 30; systemctl is-failed home-assistant-bootstrap.service music-assistant-setup.service; sudo journalctl -u music-assistant-setup -n 20 --no-pager'
```

Expected: both units report `inactive` (finished) or `active`, not `failed`, and the
Music Assistant setup log shows no login error.

- [ ] **Step 7: Android companion app (the user)**

The user clears the app's data (or removes the server), adds `https://ha.<domain>`,
follows the device-code prompt in Chrome and lands in the app. Meanwhile:

```bash
ssh admin@<server> 'sudo journalctl -u home-assistant -f -n0 | grep -iE "auth_oidc|login|error"'
```

Expected: the app connects; no errors from `auth_oidc` in the log.

- [ ] **Step 8: Remove the leftover proxy objects in Authentik**

First open the "Home Assistant (proxy)" application's Policy / Group / User Bindings and
make sure everyone bound there is in the `home-assistant` group. Then, in the Authentik
admin UI, delete the "Home Assistant (proxy)" application and provider.
Then check that `https://ha.<domain>` still logs in.

---

### Task 5: Turn automatic linking off

Run only after Task 4, Step 5 has shown the `auth_oidc` credential on `akadmin`.

**Files:**
- Modify: `services/home-assistant.nix` (the `auth_oidc.features` block)
- Modify: `tests/ha-oidc-login.nix` (the features case)

- [ ] **Step 1: Change the test to expect linking off and watch it fail**

In `tests/ha-oidc-login.nix`, replace the features case with:

```nix
    (expect "auth_oidc redirects and links nobody automatically" (
      (oidc.features or { }) == {
        default_redirect = true;
        automatic_user_linking = false;
      }
    ))
```

```bash
cd /home/traph/projects/lanbat/nixos
nix build -L path:.#checks.x86_64-linux.ha-oidc-login
```

Expected: FAIL on that case.

- [ ] **Step 2: Turn linking off**

In `services/home-assistant.nix`, set `automatic_user_linking = false;` in
`auth_oidc.features`, and change the comment above `auth_oidc` from "Linking stays on
only until each of them has logged in once" to "Linking is off; docs/auth.md says how to
link a new person".

- [ ] **Step 3: Run the test, deploy and check**

```bash
cd /home/traph/projects/lanbat/nixos
nix build -L path:.#checks.x86_64-linux.ha-oidc-login
```

Expected: PASS. Deploy as in Task 4, Step 2, and the user logs in once more through
Authentik in a private window to confirm the linked login still works.

- [ ] **Step 4: Commit**

```bash
cd /home/traph/projects/lanbat/nixos
nix fmt -- services/home-assistant.nix tests/ha-oidc-login.nix
git add services/home-assistant.nix tests/ha-oidc-login.nix
git commit -m "Home Assistant: turn auth_oidc's automatic user linking off

akadmin is linked. Linking left on would let any Authentik account whose
username matches an HA user take that user over.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
