# modules/core/default.nix
#
# Imported by every host: settings, the service interface, shared system
# configuration, and the wiring that applies on any host.
{
  imports = [
    ./settings.nix
    ./host-context.nix
    ./services.nix
    ./database.nix
    ./auth.nix
    ./overlay.nix
    ./base.nix
    ./gc.nix
    ./ssh.nix
    ./users.nix
    ./human-users.nix
    ./voice-satellite.nix
    ./snapclient.nix
    ./lan-bridge.nix
    ./wifi-access-point.nix
    ../wiring/accounts.nix
    ../wiring/secrets.nix
    ../wiring/checks.nix
    ../wiring/policy.nix
  ];
}
