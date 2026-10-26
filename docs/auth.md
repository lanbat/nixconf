# Authentication & access control

Authentik is the single identity provider for every service. One set of credentials
signs you in to the whole lab. This page is the reference for **who the users are, how
they are created, and how access to each service is granted** — the part of the
architecture that lives *outside* the Nix evaluation.

The short version, and the rule that makes the whole design work:

- **Nix declares the *providers* and *applications*** — the blueprints that say "this
  service is an SSO target with this client ID and these redirect paths."
- **Humans create the *users*, *groups*, and *policy bindings*** — in the authentik web
  UI, never in Nix.

A user that Nix cannot see is a user that no service can see. Everything downstream of
authentik (Home Assistant, Grafana, Nextcloud, …) treats an authentik username as the
source of truth.

## The identity provider

Authentik runs on the always-on tier as two rootless containers
(`services/authentik/default.nix`), so SSO is available at boot with no LUKS unlock:

- `authentik-server` (web console + API + **embedded outpost**) — `auth.<domain>`
- `authentik-worker` (async jobs: the provider/application blueprints import here)

State lives on the **always-on** PostgreSQL instance (not the workload one) and the
shared Redis (`lanbat.redis.databases.authentik.index = 0`). The version is pinned
(`2024.12.2`), so a NixOS update does not silently upgrade the IdP.

The **embedded outpost** in `authentik-server` is what answers Caddy's forward-auth
calls at `/outpost.goauthentik.io/auth/caddy` and what serves the OIDC endpoints
(`/application/o/…`). No separate outpost container is deployed.

Two agenix secrets feed it (`secrets/secrets.nix`):

- `authentik-env` — two lines: `AUTHENTIK_POSTGRESQL__PASSWORD` and `AUTHENTIK_SECRET_KEY`.
- `authentik-oidc-secrets` — one `AUTHENTIK_<NAME>_CLIENT_SECRET` line per OIDC client,
  the value copied back from the application after it is created in the authentik UI
  (chicken-and-egg: Nix generates the client, a human reads its secret into the file).

## The two ways a service authenticates you

Every service with a web UI authenticates users through **one of two mechanisms**, both
generated from the service description by `services/authentik/catalogue.nix`. There is
no third path and no per-service password except where a service keeps its own account
system (Jellyfin, Vaultwarden).

### Forward auth (Caddy asks authentik first)

Used by services with no auth of their own. Caddy intercepts the browser request and
`forward_auth`s it to the embedded outpost. If there is no valid authentik session the
outpost redirects to the `auth.<domain>` login; if there is, Caddy forwards the request
and the outpost's identity headers ride along:

```
browser ──▶ Caddy ──forward_auth──▶ authentik outpost ──▶ 302 to /application/authorize/
browser ──▶ authentik login ──▶ back to Caddy with session cookie
browser ──▶ Caddy (cookie ok) ──▶ outpost (200 + X-Authentik-*) ──▶ service
```

The forward-auth block is defined in `lanbat.authProvider.forwardAuth`
(`services/authentik/default.nix`) and wired into each vhost by
`modules/wiring/caddy.nix`. It copies the identity headers —
`X-Authentik-Username`, `X-Authentik-Groups`, `X-Authentik-Entitlements`,
`X-Authentik-Email`, `X-Authentik-Name`, `X-Authentik-Uid`, `X-Authentik-Jwt` (plus the
`X-Authentik-Meta-*` JWT metadata) — onto the request to the service.

Forward auth gates the **whole UI**. Any fine-grained permission is the downstream
service's job, not Caddy's. A service that is *called directly by clients* (companion
apps, sync clients) sets `apiClients = true`, and Caddy then bypasses forward auth for
`/auth/token*`, `/api/*` (and `/.well-known/immich`) so those clients can talk to the
app while the browser UI stays gated.

### OIDC (the service does the OAuth2 dance itself)

Used by services with a native OpenID Connect integration. The service redirects the
browser to `auth.<domain>/application/o/authorize/`, and authentik returns an ID token
the service exchanges and verifies. Caddy only has to route the **redirect/callback
paths** straight through to the service (no forward auth on them). A `forward-auth`
service can be an OIDC client at the same time (Immich is both).

```
browser ──▶ service ──▶ 302 to auth.<domain>/application/o/authorize/
browser ──▶ authentik login ──▶ 302 back to <service>/<redirectPath> with ?code=
browser ──▶ service ──token exchange──▶ auth.<domain>/application/o/token/
browser ──▶ service (sets its own session from the ID token)
```

### Which is which

| Service | `auth` | OIDC client | Notes |
|---|---|---|---|
| Home Assistant | app | yes | OIDC login (`auth_oidc`, see below); companion apps sign in with a device code |
| Frigate, Music Assistant, Snapcast, Zigbee2MQTT, Syncthing, qBittorrent, Bitmagnet | forward-auth | no | pure forward auth |
| Immich | forward-auth | yes | web UI forward-authed; mobile app OIDC + `apiClients` |
| Grafana | app | yes | `generic_oauth`, maps roles from a claim (see below) |
| Nextcloud | app | yes | `user_oidc` app; sync clients via `apiClients` |
| RomM, Audiobookshelf | app | yes | native OIDC; apps pair/call the API via `apiClients` |
| Jellyfin | app | yes | **own account system** plus OIDC (via `jellyfin-bootstrap`) |
| Vaultwarden | app | yes | **own account system** (Bitwarden) plus OIDC; SSO signs in by matching the email to an existing account (created from the admin panel or its API), gated on the `vaultwarden` group |
| Invidious | forward-auth | no | only the personal pages are gated; browsing, watching and the API bypass Authentik |
| Homepage, SearXNG, Redlib, CA page | none | no | deliberately open |

`auth` defaults to `"app"` (Caddy passes through, the service owns the login). Set it
to `"forward-auth"` for services that have no login of their own, or when the parts
of a service that expose personal data should sit behind Authentik (Invidious uses it
for its account pages only; `caddy.authBypassPaths` keeps browsing open).

Invidious has **no OIDC/SSO**: Authentik gates its personal pages and forwards
`X-Authentik-*` headers, but Invidious ignores them. Its "logged in" state is
always an Invidious-local account (`/login`), separate from Authentik — signing
into Authentik does not sign you into Invidious.

## How the catalog is generated

`services/authentik/catalogue.nix` walks `lanbat.services` and renders, per service, a
**provider + application** pair as YAML, plus the list of proxy providers the embedded
outpost serves. The files are dropped into the `authentik-server` container under
`/blueprints/custom/` and imported at startup with `state: present` (idempotent —
re-apply, don't delete).

- `auth = "forward-auth"` → a proxy **provider** (`provider-<name>`, mode
  `forward_single`, pointing at `https://<subdomain>.<domain>`) and an **application**
  (slug `<name>`), and the service is added to the embedded outpost's provider list.
- `oidc` present → an **OAuth2 provider** (`provider-<name>`, `client_id` = the service
  name) and an **application** (slug `<name>`) with the service's `redirectPaths` as its
  redirect URIs.

When a service is **both** forward-auth and an OIDC client (Immich), the OIDC objects
keep the plain names and the **proxy** side takes the `-proxy` suffix (`provider-<name>-proxy`,
slug `<name>-proxy`); the OIDC application keeps the real launch URL (Authentik's "Log back into
<app>" link after sign-out uses it) but is hidden (`meta_hide`), so the service shows
once in "My applications", through the proxy.

The application is created with `policy_engine_mode = "any"` and **no policy bound**,
which means any authenticated authentik user can reach it by default. Nothing in the
blueprint creates a group or restricts an application — that is the manual step below.

The YAML uses authentik's own tags (`!Find`, `!KeyOf`, `!Env`) so objects are matched by
a stable field rather than by Nix's `uid`, and client secrets are read from
`!Env AUTHENTIK_<NAME>_CLIENT_SECRET` rather than being baked into the blueprint. The
writer is `services/authentik/yaml.nix`; the import job is `services/authentik/blueprints.nix`.

**Identifier stability contract** (do not break this — it is what keeps re-deploys
idempotent):

- proxy provider name = `provider-<name>` (or `provider-<name>-proxy` when the service is also an OIDC client)
- OIDC provider name = `provider-<name>`
- application slug = `<name>` (or `<name>-proxy` for the proxy side of a co-located service)

A service whose identity is only in the UI (its user/group bindings, the copied-back
client secret) will be *orphaned* if these identifiers change — the blueprint will
create a new provider/application and the old one, with its bindings, stays behind.

## Users, groups, and who-can-login (the manual layer)

This is the part that is **not** in Nix. You manage it in the authentik web UI at
`auth.<domain>` (the day-to-day flow is in
[docs/operations.md → Adding a user](operations.md#adding-a-user)):

1. **Create the user** — Directory → Users → Create (username, name, email, password).
2. **Restrict a service to a set of people (optional)** — the applications the blueprint
   creates are open to *every* authenticated authentik user by default. To gate one,
   create a **group** (Directory → Groups), add the intended users to it, and bind the
   group to the application on the provider's **Policy / Group Bindings** tab.

The blueprints give you the *scaffolding* — providers and applications. The *content*
— the actual user accounts, the groups, and which applications a group may reach — is
yours to fill in. Whether a particular service is gated at all is a per-site decision,
not something Nix enforces.

## How entitlements are structured (the layering)

Access is decided in **two independent layers**, and it is important not to blur them:

| Layer | Decides | Where | Declared by |
|---|---|---|---|
| **1. Can this user reach the app at all?** | whether authentik lets the user authenticate to this service's application — open to every authenticated user by default, optionally restricted to a bound group | authentik (the application's policy / group bindings) | human, in the authentik UI |
| **2. What can the user do *inside* the app?** | the in-app role / permissions / ownership | each downstream service | the service's mapping of the authentik identity (below) |

The *mechanism* of layer 1 is the same for every service; whether a gate is actually
applied to a given service is a per-site decision. Layer 2 is per-service and is where the
authentik identity gets *translated* into something the service understands. The
translation pattern differs by service:

- **OIDC login linked by username (Home Assistant).** HA's own login page hands over to
  authentik over OpenID Connect (the `auth_oidc` component, `services/home-assistant.nix`).
  The `preferred_username` claim links to the HA user of the same name, which the
  `home-assistant-bootstrap` oneshot provisions from `lanbat.homeAssistant.ssoUsers`
  (default `["akadmin"]`). The component creates an HA user for any other identity it is
  handed, so who may log in at all is the `home-assistant` authentik group:
  `access.groups` binds it to the application, and `roles.user` refuses anyone outside
  it. Entitlement inside HA is HA's own (owner/admin). The companion apps use the same
  hostname and sign in with a device code entered in the phone's browser. Caddy does not
  forward-auth HA: the apps start the login in a WebView and finish it in the browser,
  which never has the outpost's session cookie. HA's password login stays at
  `?skip_oidc_redirect=true`.

  **Adding a person:** add them to the `home-assistant` group in authentik. Their first
  login through authentik creates their HA user. Do not also list them in
  `lanbat.homeAssistant.ssoUsers` while linking is off, or they end up with two HA
  users: the bootstrap's and the component's. To link a person to an HA user that
  already exists instead, set `automatic_user_linking = true` in
  `services/home-assistant.nix`, deploy, have them log in once, then set it back to
  `false` and deploy again. While linking is on, any member of the group whose username
  matches an HA user takes that user over.

  **After switching from forward auth:** add the owner (`akadmin`) and everyone else
  who used HA to the `home-assistant` group before their next login. Group bindings
  made by hand on the old "Home Assistant (proxy)" application no longer apply. Then,
  since authentik keeps objects that drop out of the blueprint, delete that provider
  and application in the authentik UI (Applications → Providers, and
  Applications → Applications).
- **Role mapped from a claim (Grafana).** `generic_oauth` is enabled with
  `role_attribute_path = "contains(groups[*], 'grafana-admins') && 'Admin' || 'Viewer'"`
  (`services/grafana.nix`). Every authentik user lands as **Viewer** by default; being
  in the `grafana-admins` group promotes them to **Admin**. This is entitlement expressed
  as *a group name carried in the OIDC `groups` claim*.
- **Email-matched admin (Nextcloud, Immich, Audiobookshelf, Jellyfin).** These create an
  *admin* account whose email is derived from the owner's authentik identity
  (`lanbat.deployment.immich.adminEmail` defaults to `head ssoUsers @ rootDomain`, e.g.
  `akadmin@10ctr.vg.cd`). When that same identity signs in over OIDC, the provider maps
  the token to the existing admin account rather than minting a new one.
- **Own account system (Jellyfin, Vaultwarden).** The service keeps its own users.
  Jellyfin's `jellyfin-bootstrap` also configures an Authentik OIDC login (only when
  Home Assistant is present too), so an authentik session lands on the matching Jellyfin
  account. Vaultwarden keeps Bitwarden accounts, but its OIDC is wired too: SSO signs in
  by matching the email to an account that already exists (invite it from the admin
  panel first), gated on the `vaultwarden` group, and the `offline_access` scope hands
  Bitwarden clients a refresh token. For these, layer-1
  reach and layer-2 identity are deliberately *decoupled*.

So the same question — "what can this user do?" — is answered in authentik (can they
even get in) and again inside each app (what they get once they're in), and the glue
between the two is a per-service mapping of the authentik username / email / groups.

## The owner identity and the bootstrap chain

Two identities are in play:

- **The break-glass owner, `akadmin`** — the initial admin, created in the authentik
  UI, and the account the whole bootstrap chain hangs off
  (`lanbat.homeAssistant.ssoUsers` defaults to `["akadmin"]`). HA is the **pivot
  service**: because its bootstrap can call the HA REST API with the owner's
  credentials, several *other* services bootstrap themselves **through Home
  Assistant** rather than directly against authentik:
  - Music Assistant, Jellyfin and Audiobookshelf read `HA_LONG_LIVED_TOKEN` and
    `OWNER_USERNAME`/`OWNER_PASSWORD` from the shared `hass-bootstrap-env` secret
    (declared by HA, consumed via `readsSecrets`) and log in to HA as the owner to
    complete their own setup. Music Assistant also signs in to Audiobookshelf as that
    owner (the root account `audiobookshelf-bootstrap` creates) for its Audiobookshelf
    provider.
  - The Immich and Jellyfin admin emails are derived from the owner's identity.
  `authentik-env` carries only the PostgreSQL password and secret key.
- **The day-to-day owner, `momchilov`** — the account the household actually signs in
  with. It gets the owner's per-service access (Home Assistant, the Syncthing web UI,
  ...) but is **not** part of the bootstrap chain: `akadmin` keeps `ssoUsers`,
  `OWNER_USERNAME`, the Immich/Jellyfin admin emails and the RomM admin unchanged.

`akadmin` stays break-glass: if the day-to-day account is ever locked out, it still
has full owner access.

### Declarative users and groups

The instance's users and groups are managed declaratively by the
`lanbat.deployment.authentik` option, rendered into the `users` blueprint
(`30-users.yaml`) by `services/authentik/catalogue.nix`. Each declared user is created
**without a password** — set that in the instance's UI — and is then brought to exactly
its declared name, email and full group membership, so the declared values are the
account's whole truth afterwards; the groups it names are created if missing.
Blueprints only add and update: removing a user or group from the option does not
delete it, so delete those in the UI.

In practice: declare `momchilov` (and `akadmin`) under `deployment.authentik`, set
momchilov's password in the authentik UI, sign in to HA once (which provisions the
owner there), and the downstream services that bootstrap through HA pick the owner up
automatically. Every *other* user is added the same way: one entry under
`deployment.authentik`, a password in the UI, and the per-service accounts.

## Non-SSO identity: POSIX accounts and storage

SSO covers the **web/browser** path. The lab also has a parallel identity for things
that are not SSO:

- `lanbat.humanUsers` (`modules/core/human-users.nix`) declares **matching Linux
  accounts** (same UID on server and Pi) with XFS project quotas. This is for local
  shell access and file ownership — *not* for service login. authentik remains the IdP
  for the services; `humanUsers` just makes the same person a real local user with a
  stable UID and a storage quota.
- **Samba** keeps local `smbpasswd` users (same usernames as authentik, plus a `private`
  group that gates the hidden share). **Mosquitto** uses a local password file with one
  credential per MQTT *client* (Home Assistant, Frigate, Zigbee2MQTT), not per human.

The two identities (authentik user ↔ POSIX user) are intentionally separate and are kept
in sync by convention (same name/UID), not by a technical link.

## Request flow, end to end

Forward-auth service (e.g. Frigate):

```
browser ─▶ Caddy (nvr.<domain>)
          └─ no authentik session? ─▶ 302 auth.<domain>/application/authorize/frigate
                └─▶ authentik login (akadmin) ─▶ 302 back with session cookie
          └─ session ok ─▶ outpost 200 + X-Authentik-* ─▶ Frigate (no auth of its own)
```

OIDC service (e.g. Grafana):

```
browser ─▶ Caddy (grafana.<domain>, auth="app") ─▶ Grafana
          └─ Grafana: no session ─▶ 302 auth.<domain>/application/o/authorize/?client_id=grafana
                └─▶ authentik login (akadmin) ─▶ 302 back to /login/generic_oauth?code=…
          └─ Grafana exchanges code ─▶ sets session ─▶ role from groups claim (Viewer/Admin)
```

## Where things live

| Concern | Location |
|---|---|
| authentik containers, secrets, bootstrap | `services/authentik/default.nix` |
| catalog → blueprint generation | `services/authentik/catalogue.nix` |
| declarative users & groups (`deployment.authentik`) | the `users` blueprint, `30-users.yaml` |
| blueprint import (worker) | `services/authentik/blueprints.nix` |
| YAML writer (`!Find`/`!KeyOf`/`!Env`) | `services/authentik/yaml.nix` |
| auth-provider contract | `modules/core/auth.nix` |
| Caddy forward-auth wiring | `modules/wiring/caddy.nix` |
| service description schema (`auth`, `oidc`, `apiClients`) | `modules/core/services.nix` |
| POSIX users + storage quotas | `modules/core/human-users.nix` |
| per-service identity mapping | `services/<name>.nix` (HA, Grafana, Immich, Nextcloud, Jellyfin, RomM, Audiobookshelf) |
| adding a user, day to day | [docs/operations.md](operations.md#adding-a-user) |
| what "always-on vs workload" means for access at boot | [docs/secure-layers.md](secure-layers.md) |
