{
  lib,
  fetchFromGitHub,
  buildHomeAssistantComponent,
  home-assistant,
}:

buildHomeAssistantComponent rec {
  owner = "jekalmin";
  domain = "extended_openai_conversation";
  version = "2.0.3";

  src = fetchFromGitHub {
    owner = "jekalmin";
    repo = "extended_openai_conversation";
    tag = version;
    hash = "sha256-jGOL7CmdJzZ/5lW+9aMz5MkiHK8X/9wFYA6tacK1laI=";
  };

  dependencies = [ home-assistant.python3Packages.openai ];

  # The manifest pins openai~=2.21.0; Home Assistant ships a newer 2.x.
  ignoreVersionRequirement = [ "openai" ];

  doCheck = false;
  dontBuild = true;

  meta = {
    description = "Home Assistant conversation agent for any OpenAI-compatible chat completions API";
    homepage = "https://github.com/jekalmin/extended_openai_conversation";
    license = lib.licenses.mit;
  };
}
