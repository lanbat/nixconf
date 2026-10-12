# pkgs/voice-id/default.nix
#
# voice-id: the Wyoming speech-to-text proxy that identifies the speaker on
# the side (services/voice-id.nix, docs/people.md). The CAM++ speaker-embedding
# model comes with it as `model`.
{
  fetchurl,
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "voice-id";
  version = "1.0";
  pyproject = true;
  src = ./.;
  build-system = [ python3Packages.setuptools ];
  dependencies = [
    python3Packages.aiohttp
    python3Packages.sherpa-onnx
    python3Packages.wyoming
  ];
  nativeCheckInputs = [
    python3Packages.pytestCheckHook
    python3Packages.pytest-aiohttp
    python3Packages.pytest-asyncio
  ];
  passthru.model = fetchurl {
    # 3D-Speaker CAM++, trained on VoxCeleb, 16 kHz, 512-dimensional embeddings.
    url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx";
    hash = "sha256-NXqDT3ArgBYeW5gRgsA44YVTwfLKdS7WzsIFI2XUEps=";
  };
  meta = {
    description = "Wyoming speech-to-text proxy that identifies the speaker";
    mainProgram = "voice-id";
  };
}
