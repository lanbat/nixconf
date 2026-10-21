# plugins/follow-me/default.nix
#
# Music that follows a person from room to room, and lights and music that
# switch off in an empty room or on in a dark one (modules/server/follow-me.nix,
# docs/people.md). Needs the assistant router's "Where is" sensors in Home
# Assistant; with lanbat.homeAssistant.followMe.enable unset it does nothing.
{
  name = "lanbat-follow-me";
  version = 1;
  roles = [ "server" ];
  modules = [
    ../../modules/server/follow-me.nix
  ];
}
