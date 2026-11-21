# plugins/lighting/default.nix
#
# Lights that come on when it gets dark and off in the early morning, and
# hallway lights that follow someone from the entrance into the hall
# (modules/server/lighting.nix). Needs Home Assistant's sun integration; with
# lanbat.homeAssistant.lighting.enable unset it does nothing.
{
  name = "lanbat-lighting";
  version = 1;
  roles = [ "server" ];
  modules = [
    ../../modules/server/lighting.nix
  ];
}
