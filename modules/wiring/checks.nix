# modules/wiring/checks.nix
#
# Evaluation-time checks on lanbat.services. A failing check stops the build
# with a message that starts with "lanbat:".
{ config, lib, ... }:

let
  services = lib.mapAttrsToList (name: svc: svc // { inherit name; }) config.lanbat.services;

  # A consumed name may be provided from another host, so the profile-wide table
  # is the authority. It is empty on a host assembled without one, in which case
  # the local services are all there is to go on.
  providedInProfile = name: (config.lanbat.endpoints ? ${name}) || (config.lanbat.services ? ${name});

  # [ { key; owner; } ] → error lines for keys claimed by more than one owner.
  clashes =
    what: claims:
    let
      byKey = lib.groupBy (c: toString c.key) claims;
      owners = lib.mapAttrs (_: cs: lib.unique (map (c: c.owner) cs)) byKey;
    in
    lib.mapAttrsToList (key: os: "lanbat: ${what} ${key} is used by ${lib.concatStringsSep ", " os}") (
      lib.filterAttrs (_: os: lib.length os > 1) owners
    );

  portClaims = lib.concatMap (
    s:
    map
      (p: {
        key = p;
        owner = s.name;
      })
      (
        lib.optional (s.port != null) s.port
        ++ lib.optional (s.onDemand != null) s.onDemand.activatorPort
        ++ s.extraPorts
      )
  ) services;

  subdomainClaims = lib.concatMap (
    s:
    lib.optional (s.subdomain != null) {
      key = s.subdomain;
      owner = s.name;
    }
  ) services;

  secretClaims = lib.concatMap (
    s:
    map (secret: {
      key = secret;
      owner = s.name;
    }) (lib.attrNames s.secrets)
  ) services;

  uidClaims = lib.mapAttrsToList (name: u: {
    key = u.uid;
    owner = name;
  }) (lib.filterAttrs (_: u: u.uid != null) config.users.users);

  gidClaims = lib.mapAttrsToList (name: g: {
    key = g.gid;
    owner = name;
  }) (lib.filterAttrs (_: g: g.gid != null) config.users.groups);

  # Gated units start with workload-online.target. A unit outside the gate that
  # pulls one in, or needs a workload directory, pulls in the workload mounts
  # too, and boot then waits for a LUKS device that only unlock-workload opens.
  workload = lib.filter (s: s.tier == "workload") services;
  gatedUnits = lib.concatMap (s: s.units) workload;
  gatedDirs = [ "/mnt/workload" ] ++ map (d: "/var/lib/${d}") (lib.concatMap (s: s.state) workload);
  onWorkload = path: lib.any (d: path == d || lib.hasPrefix "${d}/" path) gatedDirs;
  words = v: if lib.isList v then v else lib.splitString " " (toString v);

  gatedPulls = lib.concatLists (
    lib.mapAttrsToList (
      name: unit:
      map (dep: "lanbat: ${name} pulls in the workload-gated ${dep}; add ${name} to that service's units")
        (
          lib.intersectLists (map (u: "${u}.service") gatedUnits) (
            unit.wants ++ unit.requires ++ unit.bindsTo ++ unit.requisite or [ ]
          )
        )
      ++
        map
          (
            path:
            "lanbat: ${name} needs ${path}, which is on the workload layer; add ${name} to that service's units"
          )
          (
            lib.filter onWorkload (
              words (unit.unitConfig.RequiresMountsFor or [ ])
              ++ words (unit.serviceConfig.WorkingDirectory or [ ])
              ++ map (d: "/var/lib/${d}") (words (unit.serviceConfig.StateDirectory or [ ]))
            )
          )
    ) (removeAttrs config.systemd.services gatedUnits)
  );

  # A timer, socket or path unit starts the service of the same name, so for a
  # gated service it has to start with the layer rather than at boot.
  ungatedTriggers =
    lib.concatMap
      (
        kind:
        map
          (
            name:
            "lanbat: ${name}.${kind} starts the workload-gated ${name}.service outside the gate; set systemd.${kind}s.${name}.wantedBy to [ \"workload-online.target\" ]"
          )
          (
            lib.filter (
              name: lib.any (t: t != "workload-online.target") config.systemd."${kind}s".${name}.wantedBy
            ) (lib.intersectLists gatedUnits (lib.attrNames config.systemd."${kind}s"))
          )
      )
      [
        "timer"
        "socket"
        "path"
      ];

  # Units the wiring attaches to must be defined by some module, or the
  # generated overrides create empty units that fail at runtime.
  referencedUnits = lib.concatMap (
    s:
    map
      (unit: {
        inherit unit;
        owner = s.name;
      })
      (
        lib.optionals (s.tier == "workload") s.units
        ++ s.nfs.units
        ++ lib.optional (s.onDemand != null) (lib.removeSuffix ".service" s.onDemand.unit)
      )
  ) services;

  undefinedUnits = lib.filter (
    r: (config.systemd.services.${r.unit}.serviceConfig.ExecStart or null) == null
  ) referencedUnits;

  perService = s: [
    {
      # consumes is a hard requirement: the wiring has to resolve every name to
      # something that actually runs. An optional integration is expressed with
      # lanbat.hasService instead, and drops out of consumes when absent.
      #
      # The name may be provided by any host in the profile — that is the whole
      # point of consumes — so this looks in the profile-wide table first and
      # falls back to the local services for a host assembled without one, as
      # the pure-eval tests are.
      assertion = lib.all (name: providedInProfile name) s.consumes;
      message =
        "lanbat: ${s.name} consumes "
        + lib.concatStringsSep ", " (lib.filter (name: !(providedInProfile name)) s.consumes)
        + ", which no service in this profile provides. Add it to a host in this"
        + " profile, or make the integration conditional on lanbat.hasService.";
    }
    {
      assertion = s.access.groups == [ ] || s.auth == "forward-auth" || s.oidc != null;
      message = "lanbat: ${s.name} limits access to Authentik groups, but Authentik guards neither its web UI (auth = \"forward-auth\") nor its login (oidc), so nothing would enforce it";
    }
    {
      assertion = s.auth != "forward-auth" || config.lanbat.authProvider != null;
      message = "lanbat: ${s.name} uses forward auth, but no authentication provider runs on this host";
    }
    {
      assertion = s.tier != "workload" || s.state != [ ];
      message = "lanbat: ${s.name} is workload-gated but declares no state; list its /var/lib directories in state";
    }
    {
      assertion = s.tier != "workload" || s.units != [ ];
      message = "lanbat: ${s.name} is workload-gated but declares no units to gate";
    }
    {
      assertion = s.tier == "workload" || s.state == [ ];
      message = "lanbat: ${s.name} declares state but isn't workload-gated; set tier = \"workload\"";
    }
    {
      assertion = s.nfs.drives == [ ] || s.nfs.units != [ ];
      message = "lanbat: ${s.name} uses Pi storage but declares no units to stop when it disappears";
    }
    (
      # The drive set is whatever the storage host declares, so a drive name is
      # only valid against that host's storage.drives.
      let
        storageHost =
          if s.nfs.storageHost == null then config.lanbat.deployment.primaryStorage else s.nfs.storageHost;
        host = config.lanbat.hosts.${storageHost} or null;
        available = if host == null then [ ] else lib.attrNames host.storage.drives;
        missing = lib.subtractLists available s.nfs.drives;
      in
      {
        assertion = s.nfs.drives == [ ] || (host != null && host.role == "storage-pi" && missing == [ ]);
        message =
          if storageHost == null || host == null then
            "lanbat: ${s.name} uses Pi storage, but no storage-pi host in this profile exports it"
          else if host.role != "storage-pi" then
            "lanbat: ${s.name} takes Pi storage from ${storageHost}, which is not a storage-pi host"
          else
            "lanbat: ${s.name} uses drive ${lib.concatStringsSep ", " missing}, which ${storageHost} does"
            + " not have; its drives are ${lib.concatStringsSep ", " available}";
      }
    )
    {
      # The wiring comes with the host's role, not with the service, so a
      # description placed on a host without it would otherwise be ignored:
      # the service would start at boot, or never stop when idle.
      assertion = s.onDemand == null || config.lanbat.wiring.onDemand;
      message =
        "lanbat: ${s.name} is on-demand, but this host has no on-demand wiring"
        + " (modules/wiring/on-demand.nix, which the server role imports). Place it"
        + " on a server host, or drop onDemand.";
    }
    {
      assertion = s.tier != "workload" || config.lanbat.wiring.workloadGate;
      message =
        "lanbat: ${s.name} is workload-gated, but this host has no workload gate"
        + " (modules/wiring/workload-gate.nix, which the server role imports), so its"
        + " state would sit on the host root and its units start at boot. Place it"
        + " on a server host, or leave it always-on.";
    }
    {
      assertion = s.onDemand == null || s.port != null;
      message = "lanbat: ${s.name} is on-demand but has no port for the activator to proxy to";
    }
    {
      assertion = s.subdomain == null || s.port != null || s.caddy.extraConfig != "";
      message = "lanbat: ${s.name} has a subdomain but neither a port nor caddy.extraConfig";
    }
    {
      assertion = s.dashboard == null || s.subdomain != null;
      message = "lanbat: ${s.name} is on the dashboard but has no subdomain to link to";
    }
  ];

  # Tang must stay outside generated policy and off the overlay. The Pi's Clevis
  # reaches it to unlock the storage LUKS volumes, and Clevis is not a service
  # that can declare it consumes Tang, so an endpoint would make policy.nix drop
  # the Pi on port 7500. Keeping Tang endpoint-less is also what keeps it off
  # the overlay: transport is a property of an endpoint, so a service without
  # one stays on the LAN whatever the profile runs.
  tangEndpoint =
    lib.optional ((config.lanbat.services.tang or { endpoint = null; }).endpoint != null)
      (
        "lanbat: tang publishes an endpoint. It must not: policy would drop the"
        + " storage Pi's Clevis, which unlocks the Pi's storage through it. Keep 7500"
        + " in extraPorts and the literal firewall rule in services/tang.nix."
      );

  # A service that declares its settings keys accepts only those, unless it
  # passes the rest through on purpose (settingsFreeform).
  unknownSettings = lib.concatMap (
    s:
    lib.optionals (!s.settingsFreeform) (
      map (
        key:
        "lanbat: ${s.name} has no setting \"${key}\"; it declares "
        + lib.concatStringsSep ", " s.settingsKeys
        + ". Fix the key, or set settingsFreeform on ${s.name} if it passes keys through."
      ) (lib.subtractLists s.settingsKeys (lib.attrNames s.settings))
    )
  ) services;

  errors =
    clashes "port" portClaims
    ++ clashes "subdomain" subdomainClaims
    ++ clashes "secret" secretClaims
    ++ clashes "UID" uidClaims
    ++ clashes "GID" gidClaims
    ++ map (r: "lanbat: ${r.owner} references unit ${r.unit}, which no module defines") undefinedUnits
    ++ gatedPulls
    ++ ungatedTriggers
    ++ tangEndpoint
    ++ unknownSettings;
in
{
  assertions =
    lib.concatMap perService services
    ++ map (message: {
      assertion = false;
      inherit message;
    }) errors;
}
