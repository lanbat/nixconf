# plugins/satellite-camera/default.nix
#
# A satellite's USB camera (the PlayStation Eye's) as a face source: faces it
# sees, when something moves, go to the assistant router for recognition
# (modules/pi/satellite-camera.nix, docs/people.md). Needs the satellite to
# have a room and the router to run.
{
  name = "lanbat-satellite-camera";
  version = 2;
  roles = [
    "server"
    "storage-pi"
    "voice-pi"
  ];
  modules = [
    ../../modules/pi/satellite-camera.nix
  ];
}
