# plugins/room-presence/default.nix
#
# Whose phone is in the room: a Bluetooth LE scan on a host with a satellite
# (a Pi, or the server) that tells the assistant router who is near
# (modules/pi/room-presence.nix, docs/people.md). Needs the satellite to have
# a room and the router to run.
{
  name = "lanbat-room-presence";
  version = 2;
  roles = [
    "server"
    "storage-pi"
    "voice-pi"
  ];
  modules = [
    ../../modules/pi/room-presence.nix
  ];
}
