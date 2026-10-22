# pkgs/satellite-camera
#
# A satellite's USB camera (the PlayStation Eye's) as a face source for the
# assistant router (satellite_camera.py, modules/pi/satellite-camera.nix,
# docs/people.md).
{
  python3Packages,
  ffmpeg-headless,
}:

python3Packages.buildPythonApplication {
  pname = "satellite-camera";
  version = "1.0";
  format = "other";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    install -Dm755 satellite_camera.py $out/bin/satellite-camera
  '';
  dependencies = [ python3Packages.websockets ];
  makeWrapperArgs = [ "--set-default FFMPEG ${ffmpeg-headless}/bin/ffmpeg" ];
  doCheck = false;
  meta = {
    description = "Send a satellite camera's faces to the assistant router, only when something moves";
    mainProgram = "satellite-camera";
  };
}
