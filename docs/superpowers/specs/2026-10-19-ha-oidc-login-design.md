# Home Assistant OpenID Connect Login Design

## Purpose

Home Assistant sits behind Caddy forward auth (Authentik) and logs users in with
`hass-auth-header`. Browsers work, but the official Android companion app cannot sign
in: the app starts `/auth/authorize` in its own WebView, where the embedded outpost sets
its session cookie on `ha.<domain>`, then opens the Authentik login on `auth.<domain>`
in Chrome. Chrome returns to `/outpost.goauthentik.io/callback` without the WebView's
cookie, and the outpost rejects it (`mismatched session ID`, `invalid state`, HTTP 400).
Bypassing forward auth for the login paths alone does not help, because the app then
loads the whole frontend in the same WebView.

Replace forward auth and header login with an OpenID Connect login inside Home
Assistant, using the `auth_oidc` custom component
([hass-oidc-auth](https://github.com/christiaangoossens/hass-oidc-auth)). Browsers and
the companion app use the one hostname, `ha.<domain>`, and both log in through
Authentik.

## Success criteria

- A browser opening `ha.<domain>` while logged out goes straight to Authentik and comes
  back logged in as the existing `akadmin` Home Assistant user (same user ID, same
  dashboards and history).
- The Android companion app logs in through Authentik. The component uses a device
  code for the apps: the app shows a code, which is entered in Chrome on the phone or on
  another computer, and the app links itself.
- A request carrying a forged `X-Authentik-Username` header logs nobody in.
- Home Assistant's own password login stays available as a fallback at
  `?skip_oidc_redirect=true`.
- The loopback clients that log in with the owner's password keep working: the
  bootstrap (`pkgs/home-assistant-bootstrap`), the Homepage token
  (`pkgs/homepage-config/create-ha-token.py`) and the Music Assistant setup
  (`pkgs/music-assistant-setup`).

## Decisions

| Question | Decision |
|---|---|
| How Authentik identities map to HA users | Link to existing HA users by username; never create new ones (`require_existing_user`). Linking is on only until each user has logged in once. |
| Logged-out browser | Straight to Authentik (`default_redirect`); the password login stays at `?skip_oidc_redirect=true`. |
| Password fallback | Stays reachable on the LAN and mesh, protected by the owner's generated 29-character password and the existing ban after 5 failed attempts. |
| TOTP on `akadmin` | Not enabled. `akadmin` is also the account the loopback clients log in as with its password, and a second factor would stop them at the code prompt. Splitting them onto a service account is possible later. |
| Component version | Package `auth_oidc` v1.2.1 under `pkgs/`. nixpkgs ships 0.6.5-alpha, which predates v1.2.0's fix for an open redirect on `/auth/welcome` (GHSA-gg9c-6c2r-6x28) and its mobile-login fixes. |
| Client type | Keep the existing confidential Authentik client; Home Assistant reads its secret. A public client would need a new `clientType` option in the blueprint generator. |

## Components

### `pkgs/home-assistant-auth-oidc/`

A `buildHomeAssistantComponent` package for `auth_oidc` v1.2.1, modelled on
`pkgs/home-assistant-auth-header/` (owner `christiaangoossens`, repo `hass-oidc-auth`,
domain `auth_oidc`). It replaces `hass-auth-header` in
`services.home-assistant.customComponents`; `pkgs/home-assistant-auth-header/` and its
reference are removed.

Removing the header component is required, not cleanup. Once the service stops being
forward-authed, Caddy no longer strips `X-Authentik-Username` from client requests, so a
header provider left installed would accept a forged header as a login.

### `services/home-assistant.nix`

- `lanbat.services.home-assistant.auth = "app"` instead of `"forward-auth"`. Caddy then
  proxies straight to Home Assistant, which matches CONTRIBUTING's rule for services with
  native OIDC. `apiClients = true` stays; the bypass list only applies to forward-authed
  services, so it has no effect once the service is `app`.
- `oidc.redirectPaths = [ "/auth/oidc/callback" ]`, replacing `/auth/external/callback`.
  The client ID stays the service name (`home-assistant`) and the secret variable stays
  `AUTHENTIK_HA_CLIENT_SECRET`.
- `readsSecrets` gains `authentik-oidc-secrets`.
- Home Assistant's YAML cannot read environment variables, so a step before Home
  Assistant starts writes `/var/lib/hass/secrets.yaml` (owner `hass`, mode 0600) with a
  single key, `auth_oidc_client_secret`, taken from `AUTHENTIK_HA_CLIENT_SECRET` in
  `config.lanbat.secrets.authentik-oidc-secrets.path`. The step fails the start when the
  variable is missing, rather than starting Home Assistant with an empty secret.
- `auth_header` is removed from the configuration, and `auth_oidc` is added:

  ```yaml
  auth_oidc:
    client_id: home-assistant
    client_secret: !secret auth_oidc_client_secret
    discovery_url: https://auth.<domain>/application/o/home-assistant/.well-known/openid-configuration
    display_name: Authentik
    features:
      default_redirect: true
      require_existing_user: true
      automatic_user_linking: true
    network:
      tls_ca_path: /var/lib/caddy-local-ca/ca-certificates.crt
  ```

  The domain comes from `config.lanbat.deployment.domain`, and the Authentik hostname
  from its service description, never a literal. `claims.username` keeps its default,
  `preferred_username`, which is the Authentik username that `ssoUsers` already lists.
- The header comment ("Auth with Authentik") and the `ssoUsers` description are
  rewritten for OIDC: the bootstrap still creates each SSO user as a Home Assistant user
  with a random password, and that username is what the component links to.

### Unchanged

- `pkgs/home-assistant-bootstrap/bootstrap-ha.sh`: it already creates SSO users with
  `hass --script auth add` and a random password, so they have the username credential
  the component links to. Only its "header auth" comment changes.
- The `homeassistant` auth provider, the owner's password and `ip_ban_enabled` with
  `login_attempts_threshold = 5`.
- Authentik's layer-1 gate: the application's policy bindings still decide who may
  authorize the `home-assistant` OIDC application.
- API clients with long-lived tokens (the voice satellites, the Homepage widget, Music
  Assistant) and `/api/*` traffic.

### Authentik

The generated blueprint (`services/authentik/catalogue.nix`) changes by itself: the
OIDC provider's redirect URI becomes `https://ha.<domain>/auth/oidc/callback`, and the
service stops getting a proxy provider, a proxy application and a place on the embedded
outpost. Authentik does not delete objects that drop out of a blueprint, so the old
"Home Assistant (proxy)" provider and its application are removed by hand once the new
login works, and that step is written into `docs/auth.md`. The OIDC application stays
the one listed in My applications.

### Documentation

- `docs/auth.md`: Home Assistant's row in the service table, the header-login paragraph
  (now OIDC with username linking), and how to add a person (below).
- `docs/architecture.md`: the auth matrix row.
- `services/home-assistant.nix` comments, as above.

## Data flow

- **Browser:** `ha.<domain>` → Home Assistant's frontend → `default_redirect` sends it
  to `/auth/oidc/...` → Authentik login → `/auth/oidc/callback` → the component matches
  `preferred_username` to the linked HA user and issues HA tokens.
- **Companion app:** the app opens Home Assistant's login on `ha.<domain>` → the
  component shows a device code → the user enters it in Chrome, where the Authentik login
  happens → the app polls, links and continues with `/auth/token`.
- **Loopback clients:** unchanged, `http://127.0.0.1:8123/auth/login_flow` with the
  `homeassistant` provider.

## Rollout

1. Branch, `nix fmt`, and evaluate `example-server` plus the `assertions`, `plugins`,
   `settings-guard` and `validate-deploy` checks.
2. On the server, confirm that `authentik-oidc-secrets` has an
   `AUTHENTIK_HA_CLIENT_SECRET` line, checking for the name without printing the value.
3. Deploy the server.
4. Verify, each step run and its result reported:
   1. A browser login lands on the same `akadmin` user, with its dashboards, still the
      owner and in `system-admin`.
   2. `curl -H 'X-Authentik-Username: akadmin'` against the frontend and the login flow
      logs nobody in.
   3. `?skip_oidc_redirect=true` shows the password login, and the owner can log in with
      it.
   4. Rerunning the Homepage token and Music Assistant setup units succeeds.
   5. The Android app logs in with the device code, with the server's logs watched
      during the attempt.
5. Remove the leftover "Home Assistant (proxy)" provider and application in Authentik.

### Follow-up change: turn linking off

The component's documentation says to keep `automatic_user_linking` on only
temporarily: while it is on, any Authentik account whose username matches an HA
username takes over that HA user. Once `akadmin` has logged in through Authentik,
a second change sets `automatic_user_linking: false` and keeps
`require_existing_user: true`. Only identities already linked can then log in.

Adding a person later: add their Authentik username to
`lanbat.homeAssistant.ssoUsers`, turn linking on, deploy, have them log in once, and
turn linking off again. `docs/auth.md` describes this.

## Failure handling

- **Authentik down:** the browser redirect fails; the password login at
  `?skip_oidc_redirect=true` still works for the owner.
- **Missing client secret:** Home Assistant does not start, and the pre-start step names
  the missing variable in the journal.
- **Unlinked or unknown username:** the component refuses the login
  (`require_existing_user`), and Authentik's policy refuses users without access before
  that.
- **Permissions:** the component documents that it sets roles only on a user's first
  login; what it does to an existing user it links is not documented. Rollout step 4.1
  checks that `akadmin` is still the owner and in `system-admin` after its first
  Authentik login. If the link changed them, `roles.admin` is set to the Authentik
  admin group before linking is used again.

## Rollback

Revert the commit and redeploy. Home Assistant's user data is not migrated: the OIDC
login is an extra credential on the existing user, and the old `auth_header` credential
stays in `.storage/auth`. The blueprint recreates the proxy provider on the next apply.

## Known limitations

- The component does not implement OpenID Connect session management or front- or
  back-channel logout. Signing out of Authentik does not end Home Assistant sessions, and
  a user disabled in Authentik keeps working until their refresh token expires or is
  revoked under Profile → Refresh tokens.
- The companion app's login needs the device code step once per phone.
- Existing browser sessions from the header login stay valid until their tokens expire.
