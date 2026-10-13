# pkgs/assistant-router
#
# The assistant router: an OpenAI-compatible endpoint between Home
# Assistant's LLM agent and the models (services/assistant-router.nix).
{ python3Packages }:

python3Packages.buildPythonApplication {
  pname = "assistant-router";
  version = "1.0";
  pyproject = true;
  src = ./.;
  build-system = [ python3Packages.setuptools ];
  dependencies = [
    python3Packages.aiohttp
    python3Packages.aiomqtt
  ];
  nativeCheckInputs = [
    python3Packages.pytestCheckHook
    python3Packages.pytest-aiohttp
    python3Packages.pytest-asyncio
  ];
  meta.mainProgram = "assistant-router";
}
