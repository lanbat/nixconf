# tests/settings-guard.nix
#
# Deployment-specific lanbat options must not have defaults. Missing values
# should fail evaluation instead of deploying placeholder IPs or domains.
{ lib, pkgs }:

let
  eval = lib.evalModules {
    modules = [
      {
        options.assertions = lib.mkOption {
          default = [ ];
        };
      }
      ../modules/core/settings.nix
      ../modules/core/host-context.nix
    ];
  };

  allowedWithDefault = [
    "profile"
    "deployment.overlay"
    "deployment.haLlm"
    "deployment.haXiaomiBle"
    "deployment.haCalendar"
    # "low-spec" is how a profile without a Mac has always run, not a placeholder.
    "deployment.voiceCompute.profile"
    "deployment.voiceRooms"
    "deployment.primaryServer"
    "deployment.primaryStorage"
    "deployment.serverIp"
    "deployment.storageIp"
    "deployment.storageHostname"
    "deployment.parkingGuard"
    "deployment.androidDevices"
    "deployment.xiaomiClocks"
    "deployment.techo5Devices"
    # Nobody to recognise is a valid household setting, not a placeholder.
    "deployment.people"
    # No declarative users is a valid instance setting, not a placeholder.
    "deployment.authentik"
    # One admin key (adminSshKey) is enough; the extra keys are optional.
    "deployment.extraAdminSshKeys"
    "deployment.dns"
    "hosts"
    "mosquitto.extraUsers"
  ];

  collectOptions =
    opts: path:
    lib.concatLists (
      lib.map (
        name:
        let
          v = opts.${name};
          childPath = path ++ [ name ];
          childName = lib.concatStringsSep "." childPath;
        in
        if v ? _type && v._type == "option" then
          [
            {
              name = childName;
              hasDefault = v ? default;
            }
          ]
        else
          collectOptions v childPath
      ) (lib.attrNames opts)
    );

  offending = lib.filter (o: o.hasDefault && !(lib.elem o.name allowedWithDefault)) (
    collectOptions eval.options.lanbat [ ]
  );
in
pkgs.runCommand "settings-guard-check" { } ''
  if [ ${toString (lib.length offending)} -ne 0 ]; then
    echo "lanbat options must not have defaults (except optional features):" >&2
    ${lib.concatStringsSep "\n" (map (o: "echo \"  - ${o.name}\" >&2") offending)}
    exit 1
  fi
  touch $out
''
