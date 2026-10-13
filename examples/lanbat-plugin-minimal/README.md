# lanbat-plugin-minimal

Minimal external lanbat plugin (contract version 2). Exports a `lanbatPlugin`
attribute that writes `/etc/lanbat-plugin-example` on server hosts — nothing
else.

This directory is an **example only**. It is not wired into the main lanbat
flake or CI; copy or fork it as a starting point for your own plugin repo.

## Layout

```
flake.nix    # exports lanbatPlugin
module.nix   # harmless environment.etc marker
```

## Use in a deployment

Add a flake input pointing at this repo (or your fork), then reference the
plugin on a server host:

```nix
# flake.nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    lanbat.url = "github:lanbat/nixconf";
    lanbat-plugin-minimal.url = "github:you/lanbat-plugin-minimal";
    lanbat-plugin-minimal.inputs.nixpkgs.follows = "nixpkgs";
  };
}
```

```nix
# deployments/<profile>/deploy.nix
hosts.server.plugins = [
  inputs.self.lanbatPlugins.services
  inputs.lanbat-plugin-minimal.lanbatPlugin
];
```

After deploy, verify on the server:

```bash
cat /etc/lanbat-plugin-example
```

See [Plugin author guide](../../docs/plugins.md) for the full contract.
