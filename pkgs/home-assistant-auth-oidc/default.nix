# pkgs/home-assistant-auth-oidc/default.nix
#
# auth_oidc (hass-oidc-auth): OpenID Connect login for Home Assistant. nixpkgs
# ships 0.6.5-alpha, older than v1.2.0's fix for an open redirect on
# /auth/welcome (GHSA-gg9c-6c2r-6x28) and its mobile-login fixes, so this
# follows the nixpkgs package at v1.2.1. Called from Home Assistant's own
# Python package set (services/home-assistant.nix), which provides
# aiofiles, jinja2 and joserfc.
{
  lib,
  buildHomeAssistantComponent,
  fetchFromGitHub,
  fetchNpmDeps,
  aiofiles,
  jinja2,
  joserfc,
  nodejs,
  npmHooks,
}:

buildHomeAssistantComponent rec {
  owner = "christiaangoossens";
  domain = "auth_oidc";
  version = "1.2.1";

  src = fetchFromGitHub {
    owner = "christiaangoossens";
    repo = "hass-oidc-auth";
    tag = "v${version}";
    hash = "sha256-vwQDrMM4phbrXT85Syyz6hWEIhLB3TKNNTM04OdvNWk=";
  };

  # The welcome and device-code pages' stylesheet is built with Tailwind.
  env.npmDeps = fetchNpmDeps {
    name = "${domain}-npm-deps";
    inherit src;
    hash = "sha256-yAm08NVz2Ln6ob9LoPfxuz6U1oBS4AEydIQHhv0WpOg=";
  };

  nativeBuildInputs = [
    npmHooks.npmConfigHook
    nodejs
  ];

  dependencies = [
    aiofiles
    jinja2
    joserfc
  ];

  postBuild = ''
    npm run css
  '';

  # Upstream's tests need a running Home Assistant test harness.
  doCheck = false;

  meta = {
    changelog = "https://github.com/christiaangoossens/hass-oidc-auth/releases/tag/v${version}";
    description = "OpenID Connect authentication provider for Home Assistant";
    homepage = "https://github.com/christiaangoossens/hass-oidc-auth";
    license = lib.licenses.mit;
  };
}
