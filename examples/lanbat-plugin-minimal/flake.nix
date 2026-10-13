{
  description = "Minimal lanbat plugin example";
  inputs.lanbat.url = "github:lanbat/nixconf";
  outputs =
    { self, ... }:
    {
      lanbatPlugin = {
        name = "example-noop";
        version = 2;
        roles = [ "server" ];
        modules = [ ./module.nix ];
      };
    };
}
