# tests/authentik-catalogue.nix
#
# The Authentik app catalogue is generated from lanbat.services
# (services/authentik/catalogue.nix). Pure evaluation of the example profile
# and variants of it:
#
#   - adding a forward-auth service through a host's modules adds its proxy
#     provider, application and outpost entry, with no blueprint edit, and
#     leaving it out removes them;
#   - an OIDC description adds an OAuth2 provider and application, and a
#     service with both keeps the two apart by the "-proxy" rule;
#   - services not placed on the host do not appear;
#   - the identifiers of the example profile's existing services are pinned,
#     because Authentik matches live objects by them.
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

  # The example profile with the server host entry changed by f.
  blueprintsWith =
    f:
    let
      deploy = exampleDeploy // {
        hosts = exampleDeploy.hosts // {
          server = f exampleDeploy.hosts.server;
        };
      };
    in
    (lanbatLib.mkProfile "example" deploy)
    .configurations.example-server.config.lanbat.authentik.blueprints;

  withModule =
    module: blueprintsWith (host: host // { modules = (host.modules or [ ]) ++ [ module ]; });

  demo = extra: {
    lanbat.services.demo = {
      subdomain = "demo";
      port = 18999;
      auth = "forward-auth";
      dashboard = {
        group = "Utilities";
        name = "Demo";
        description = "Test service";
      };
    }
    // extra;
  };

  base = blueprintsWith (host: host);
  added = withModule (demo { });
  addedWithSso = withModule (demo {
    oidc.redirectPaths = [ "/callback" ];
  });
  oidcOnly = withModule (demo {
    auth = "app";
    oidc.redirectPaths = [ "/callback" ];
  });
  restricted = withModule (demo {
    access.groups = [
      "authentik Admins"
      "family"
    ];
  });
  # Two restricted services sharing a group.
  restrictedTwice = withModule (
    { lib, ... }:
    lib.recursiveUpdate
      (demo {
        access.groups = [ "family" ];
      })
      {
        lanbat.services.demo2 = {
          subdomain = "demo2";
          port = 18998;
          auth = "forward-auth";
          access.groups = [ "family" ];
        };
      }
  );
  restrictedOidc = withModule (demo {
    auth = "app";
    oidc.redirectPaths = [ "/callback" ];
    access.groups = [ "authentik Admins" ];
  });
  # Only some services placed on the server.
  placed = blueprintsWith (
    host:
    host
    // {
      services = [
        "authentik"
        "caddy"
        "postgresql"
        "redis"
        "frigate"
        "grafana"
      ];
    }
  );

  # ── Views of a blueprint ─────────────────────────────────────────────────
  untag = v: v.value;

  entriesOf = model: bp: lib.filter (e: e.model == model) bp.entries;

  proxyProviders = bp: entriesOf "authentik_providers_proxy.proxyprovider" bp.proxy;
  oidcProviders = bp: entriesOf "authentik_providers_oauth2.oauth2provider" bp.oidc;
  appsIn = bp: entriesOf "authentik_core.application" bp;
  bindingsIn = bp: entriesOf "authentik_policies.policybinding" bp;
  # [ { app = [ "slug" <slug> ]; group = [ "name" <group> ]; } ] for one blueprint.
  bindingPairs =
    bp:
    map (e: {
      app = lib.last (untag e.identifiers.target);
      group = lib.last (untag e.identifiers.group);
    }) (bindingsIn bp);
  # Only the demo service's bindings (the example profile's Syncthing has its own).
  demoPairs =
    bp:
    lib.filter (
      p:
      p.app == [
        "slug"
        "demo"
      ]
    ) (bindingPairs bp);

  outpostProviders =
    bp: map untag (lib.head (entriesOf "authentik_outposts.outpost" bp.proxy)).attrs.providers;

  # Every identifier Authentik matches on, per blueprint.
  identity = bp: {
    proxyProviders = map (e: {
      inherit (e) id;
      name = e.identifiers.name;
    }) (proxyProviders bp);
    proxyApps = map (e: {
      slug = e.identifiers.slug;
      provider = untag e.attrs.provider;
    }) (appsIn bp.proxy);
    outpost = outpostProviders bp;
    oidcProviders = map (e: {
      inherit (e) id;
      name = e.identifiers.name;
      clientId = e.attrs.client_id;
      secret = untag e.attrs.client_secret;
    }) (oidcProviders bp);
    oidcApps = map (e: {
      slug = e.identifiers.slug;
      provider = untag e.attrs.provider;
      hidden = e.attrs.meta_hide or false;
    }) (appsIn bp.oidc);
  };

  byId = id: entries: lib.findFirst (e: (e.id or null) == id) null entries;
  bySlug = slug: entries: lib.findFirst (e: e.identifiers.slug or null == slug) null entries;

  sorted = lib.sort (a: b: a < b);

  # ── The example profile's existing identifiers ───────────────────────────
  proxied = [
    "bitmagnet"
    "frigate"
    "invidious"
    "jackett"
    "music-assistant"
    "qbittorrent"
    "snapcast"
    "syncthing"
    "zigbee2mqtt"
  ];
  names = {
    bitmagnet = "Bitmagnet";
    frigate = "Frigate";
    invidious = "Invidious";
    jackett = "Jackett";
    music-assistant = "Music Assistant";
    qbittorrent = "qBittorrent";
    snapcast = "Snapcast";
    syncthing = "Syncthing";
    zigbee2mqtt = "Zigbee2MQTT";
  };
  exampleProxyIds = map (n: "provider-${n}") proxied ++ [
    "provider-immich-proxy"
  ];
  exampleIdentity = {
    proxyProviders =
      map (n: {
        id = "provider-${n}";
        name = names.${n};
      }) proxied
      ++ [
        {
          id = "provider-immich-proxy";
          name = "Immich (proxy)";
        }
      ];
    proxyApps =
      map (n: {
        slug = n;
        provider = "provider-${n}";
      }) proxied
      ++ [
        {
          slug = "immich-proxy";
          provider = "provider-immich-proxy";
        }
      ];
    outpost = exampleProxyIds;
    oidcProviders =
      map
        (c: {
          id = "provider-${c.n}";
          name = c.name;
          clientId = c.n;
          inherit (c) secret;
        })
        [
          {
            n = "audiobookshelf";
            name = "Audiobookshelf";
            secret = "AUTHENTIK_AUDIOBOOKSHELF_CLIENT_SECRET";
          }
          {
            n = "grafana";
            name = "Grafana";
            secret = "AUTHENTIK_GRAFANA_CLIENT_SECRET";
          }
          {
            n = "home-assistant";
            name = "Home Assistant";
            secret = "AUTHENTIK_HA_CLIENT_SECRET";
          }
          {
            n = "immich";
            name = "Immich";
            secret = "AUTHENTIK_IMMICH_CLIENT_SECRET";
          }
          {
            n = "jellyfin";
            name = "Jellyfin";
            secret = "AUTHENTIK_JELLYFIN_CLIENT_SECRET";
          }
          {
            n = "nextcloud";
            name = "Nextcloud";
            secret = "AUTHENTIK_NEXTCLOUD_CLIENT_SECRET";
          }
          {
            n = "romm";
            name = "RomM";
            secret = "AUTHENTIK_ROMM_CLIENT_SECRET";
          }
        ];
    oidcApps =
      map
        (n: {
          slug = n;
          provider = "provider-${n}";
          hidden = n == "immich";
        })
        [
          "audiobookshelf"
          "grafana"
          "home-assistant"
          "immich"
          "jellyfin"
          "nextcloud"
          "romm"
        ];
  };

  # Compare as sets: entry order carries no identity.
  normalize = lib.mapAttrs (
    _: xs: sorted (map (x: if builtins.isString x then x else builtins.toJSON x) xs)
  );

  expect = name: cond: if cond then null else "FAIL: ${name}";

  hasDemo =
    bp:
    byId "provider-demo" (proxyProviders bp) != null
    || bySlug "demo" (appsIn bp.proxy) != null
    || lib.elem "provider-demo" (outpostProviders bp);

  cases = [
    (expect "the example profile keeps its identifiers" (
      normalize (identity base) == normalize exampleIdentity
    ))

    (expect "every !KeyOf in the proxy blueprint names an earlier entry" (
      let
        step =
          acc: e:
          let
            refs =
              lib.optional (e.attrs ? provider) (untag e.attrs.provider) ++ map untag (e.attrs.providers or [ ]);
          in
          {
            seen = acc.seen ++ lib.optional (e ? id) e.id;
            ok = acc.ok && lib.all (r: lib.elem r acc.seen) refs;
          };
      in
      (lib.foldl' step {
        seen = [ ];
        ok = true;
      } added.proxy.entries).ok
    ))

    (expect "without the demo service, nothing names it" (!hasDemo base))

    (expect "a restricted forward-auth service binds each group to its application" (
      demoPairs restricted.proxy == [
        {
          app = [
            "slug"
            "demo"
          ];
          group = [
            "name"
            "authentik Admins"
          ];
        }
        {
          app = [
            "slug"
            "demo"
          ];
          group = [
            "name"
            "family"
          ];
        }
      ]
    ))

    (expect "bindings come after the application they target" (
      let
        entries = restricted.proxy.entries;
        demoApp = lib.lists.findFirstIndex (e: (e.identifiers.slug or null) == "demo") null entries;
        firstBinding = lib.lists.findFirstIndex (
          e:
          e.model == "authentik_policies.policybinding"
          &&
            lib.last (untag e.identifiers.target) == [
              "slug"
              "demo"
            ]
        ) null entries;
      in
      demoApp != null && firstBinding != null && demoApp < firstBinding
    ))

    (expect "a bound group is created if missing, once, before any binding names it" (
      let
        entries = restrictedTwice.proxy.entries;
        isFamily = e: e.model == "authentik_core.group" && e.identifiers.name == "family";
        families = lib.filter isFamily entries;
        groupAt = lib.lists.findFirstIndex isFamily null entries;
        bindingAt = lib.lists.findFirstIndex (
          e: e.model == "authentik_policies.policybinding"
        ) null entries;
      in
      lib.length families == 1
      && (lib.head families).state == "created"
      && groupAt != null
      && bindingAt != null
      && groupAt < bindingAt
    ))

    (expect "a restricted OIDC service binds its OIDC application" (
      demoPairs restrictedOidc.oidc == [
        {
          app = [
            "slug"
            "demo"
          ];
          group = [
            "name"
            "authentik Admins"
          ];
        }
      ]
    ))

    (expect "an unrestricted service gets no bindings" (
      demoPairs added.proxy == [ ] && demoPairs addedWithSso.oidc == [ ]
    ))

    (expect "the example profile's Syncthing is admins only" (
      lib.elem {
        app = [
          "slug"
          "syncthing"
        ];
        group = [
          "name"
          "authentik Admins"
        ];
      } (bindingPairs base.proxy)
    ))

    (expect "the example profile's Jackett is admins only, and no other group is bound to it" (
      lib.filter (
        p:
        p.app == [
          "slug"
          "jackett"
        ]
      ) (bindingPairs base.proxy) == [
        {
          app = [
            "slug"
            "jackett"
          ];
          group = [
            "name"
            "authentik Admins"
          ];
        }
      ]
    ))

    (expect "adding a forward-auth service adds its proxy provider" (
      let
        p = byId "provider-demo" (proxyProviders added);
      in
      p != null
      && p.identifiers.name == "Demo"
      && p.attrs.mode == "forward_single"
      && lib.hasPrefix "https://demo." p.attrs.external_host
      && !(p.attrs ? internal_host)
    ))

    (expect "adding a forward-auth service adds its application" (
      let
        a = bySlug "demo" (appsIn added.proxy);
      in
      a != null && a.attrs.name == "Demo" && untag a.attrs.provider == "provider-demo"
    ))

    (expect "adding a forward-auth service adds it to the embedded outpost" (
      sorted (outpostProviders added) == sorted (outpostProviders base ++ [ "provider-demo" ])
    ))

    (expect "adding a forward-auth service leaves the others unchanged" (
      builtins.toJSON (
        lib.filter (e: e.id or "" != "provider-demo" && e.identifiers.slug or "" != "demo") (
          lib.init added.proxy.entries
        )
      ) == builtins.toJSON (lib.init base.proxy.entries)
      && builtins.toJSON added.oidc == builtins.toJSON base.oidc
    ))

    (expect "an OIDC-only service gets an OAuth2 provider and application" (
      let
        p = byId "provider-demo" (oidcProviders oidcOnly);
        a = bySlug "demo" (appsIn oidcOnly.oidc);
      in
      !hasDemo oidcOnly
      && p != null
      && p.attrs.client_id == "demo"
      && untag p.attrs.client_secret == "AUTHENTIK_DEMO_CLIENT_SECRET"
      && lib.length p.attrs.redirect_uris == 1
      && lib.hasSuffix "/callback" (lib.head p.attrs.redirect_uris).url
      && a != null
      && !(a.attrs ? meta_launch_url)
      && !(a.attrs ? meta_hide)
    ))

    (expect "a forward-auth service with OIDC gets -proxy objects beside the OIDC ones" (
      let
        p = byId "provider-demo-proxy" (proxyProviders addedWithSso);
      in
      p != null
      && p.identifiers.name == "Demo (proxy)"
      && p.attrs.internal_host == "http://127.0.0.1:18999"
      && bySlug "demo-proxy" (appsIn addedWithSso.proxy) != null
      && lib.elem "provider-demo-proxy" (outpostProviders addedWithSso)
      && byId "provider-demo" (oidcProviders addedWithSso) != null
      && (bySlug "demo" (appsIn addedWithSso.oidc)).attrs.meta_hide
      && lib.hasPrefix "https://" (bySlug "demo" (appsIn addedWithSso.oidc)).attrs.meta_launch_url
    ))

    (expect "Home Assistant's OIDC redirect URI is auth_oidc's callback" (
      let
        p = byId "provider-home-assistant" (oidcProviders base);
      in
      p != null
      &&
        map (u: u.url) p.attrs.redirect_uris == [
          "https://ha.${exampleDeploy.deployment.domain}/auth/oidc/callback"
        ]
    ))

    (expect "Home Assistant's OIDC application is bound to the home-assistant group" (
      lib.filter (
        p:
        p.app == [
          "slug"
          "home-assistant"
        ]
      ) (bindingPairs base.oidc) == [
        {
          app = [
            "slug"
            "home-assistant"
          ];
          group = [
            "name"
            "home-assistant"
          ];
        }
      ]
    ))

    (expect "RomM's OIDC redirect URI is its API OAuth callback" (
      let
        p = byId "provider-romm" (oidcProviders base);
      in
      p != null
      && lib.any (
        u: u.url == "https://romm.${exampleDeploy.deployment.domain}/api/oauth/openid"
      ) p.attrs.redirect_uris
    ))

    (expect "only services placed on the host appear" (
      map (e: e.id) (proxyProviders placed) == [ "provider-frigate" ]
      && outpostProviders placed == [ "provider-frigate" ]
      && map (e: e.id) (oidcProviders placed) == [ "provider-grafana" ]
    ))
  ];

  failures = lib.filter (x: x != null) cases;
in
pkgs.runCommand "authentik-catalogue-check" { } ''
  if [ ${toString (lib.length failures)} -ne 0 ]; then
    echo "authentik catalogue checks failed:" >&2
    ${lib.concatStringsSep "\n" (map (msg: "echo \"  - ${msg}\" >&2") failures)}
    exit 1
  fi
  touch $out
''
