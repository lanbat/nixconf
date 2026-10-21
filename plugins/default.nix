# plugins/default.nix
#
# Registry of built-in lanbat plugins, exposed as lanbatPlugins in flake.nix.
{
  services = import ./services;
  tv = import ./tv;
  voice = import ./voice;
  android = import ./android;
  follow-me = import ./follow-me;
  xiaomi-clock = import ./xiaomi-clock;
  dns = import ./dns;
  stackchan = import ./stackchan;
  room-presence = import ./room-presence;
}
