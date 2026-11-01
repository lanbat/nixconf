# pkgs/linux-voice-assistant/default.nix
#
# Linux Voice Assistant (OHF-Voice): the Open Home Foundation's replacement for
# wyoming-satellite. It talks to Home Assistant over the ESPHome protocol and
# detects the wake word on the device, which gives it continued conversation
# (the microphone reopens after a reply that asks a question), a stop word and
# timers. Not in nixpkgs.
#
# Wired by modules/core/voice-satellite-lva.nix when lanbat.voiceSatellite.backend
# is "lva".
{
  lib,
  stdenv,
  fetchFromGitHub,
  fetchurl,
  python3Packages,
  autoPatchelfHook,
  mpv-unwrapped,
}:

let
  version = "1.1.18";

  # The module always adds webrtc-noise-gain. Its bundled WebRTC code uses
  # uint32_t without including <cstdint>, which GCC 15 rejects; nixpkgs that
  # carry the upstream patch need no workaround (same as
  # modules/core/voice-satellite.nix).
  webrtcNoiseGain =
    if (python3Packages.webrtc-noise-gain.patches or [ ]) != [ ] then
      python3Packages.webrtc-noise-gain
    else
      python3Packages.webrtc-noise-gain.overridePythonAttrs (old: {
        env = (old.env or { }) // {
          NIX_CFLAGS_COMPILE = toString [
            (old.env.NIX_CFLAGS_COMPILE or "")
            "-include stdint.h"
          ];
        };
      });

  # Feature extraction for microWakeWord: C++ with the TensorFlow Lite
  # microfrontend bundled in the sdist.
  pymicro-features = python3Packages.buildPythonPackage rec {
    pname = "pymicro-features";
    version = "2.0.2";
    pyproject = true;
    src = fetchurl {
      url = "https://files.pythonhosted.org/packages/39/46/328092b4df890385594cf3d3e6015da72d77f63c58d3057276ac2353e891/pymicro_features-${version}.tar.gz";
      hash = "sha256-DQvteEPseLbO2C0aLc3etP5d9hs6+AooHQhoyOJ5xyc=";
    };
    build-system = [ python3Packages.setuptools ];
    doCheck = false;
  };

  # microWakeWord's runtime. The sdist carries only the x86_64 TensorFlow Lite
  # library, the wheels one for each architecture, so the wheel is used.
  wheels = {
    x86_64-linux = {
      url = "https://files.pythonhosted.org/packages/59/db/651fffa65447022403cb44968a2e2a717c31a33ebe17e77e1e96a6289623/pymicro_wakeword-2.5.0-py3-none-manylinux_2_35_x86_64.whl";
      hash = "sha256-OGn1NkArElTjvjVJ06Ri7NbtIc4sPnfsjrUBw8cAQXw=";
    };
    aarch64-linux = {
      url = "https://files.pythonhosted.org/packages/61/c2/764792cf610550c6e8dc06cc7ece77c0a3020054c029c907c2bbdda526f3/pymicro_wakeword-2.5.0-py3-none-manylinux_2_35_aarch64.whl";
      hash = "sha256-bHW3fJNu/Rm4w1oJTLfj/b1OvND2NjWghRzlkZkIKfI=";
    };
  };

  pymicro-wakeword = python3Packages.buildPythonPackage {
    pname = "pymicro-wakeword";
    version = "2.5.0";
    format = "wheel";
    src = fetchurl wheels.${stdenv.hostPlatform.system};
    nativeBuildInputs = [ autoPatchelfHook ];
    buildInputs = [ stdenv.cc.cc.lib ];
    dependencies = [
      pymicro-features
      python3Packages.numpy
    ];
    doCheck = false;
  };

  # pyopen-wakeword's test suite segfaults on aarch64 (it did on the Pi 3, and
  # newer nixpkgs marks the package broken there for it). LVA imports it at
  # start-up whatever the model, so it is installed without its tests. The
  # library itself ran the hey_nabu model on the Pi 3 without a crash
  # (2026-10-06), so the broken mark is overridden.
  pyopenWakeword = python3Packages.pyopen-wakeword.overridePythonAttrs (old: {
    doCheck = false;
    meta = old.meta // {
      broken = false;
    };
  });
in
python3Packages.buildPythonApplication {
  pname = "linux-voice-assistant";
  inherit version;
  pyproject = true;

  src = fetchFromGitHub {
    owner = "OHF-Voice";
    repo = "linux-voice-assistant";
    tag = "v${version}";
    hash = "sha256-QFg0mWZB6o/19fSaRfdfCF82Or7fWHK6m5DWwqdIYBc=";
  };

  env.SETUPTOOLS_SCM_PRETEND_VERSION = version;

  build-system = with python3Packages; [
    setuptools
    setuptools-scm
  ];

  # Versions are pinned exactly; nixpkgs has neighbouring ones.
  # python-mpv is provided as `mpv` (module name); types-protobuf is typing only.
  pythonRemoveDeps = [
    "python-mpv"
    "types-protobuf"
  ];
  pythonRelaxDeps = [
    "aioesphomeapi"
    "websockets"
    "numpy"
    "zeroconf"
    "soundcard"
    "netifaces2"
  ];

  dependencies =
    (with python3Packages; [
      aioesphomeapi
      netifaces2
      soundcard
      numpy
      mpv
      zeroconf
      getmac
      websockets
    ])
    ++ [
      pyopenWakeword
      pymicro-wakeword
      webrtcNoiseGain
    ];

  # Replies come from Home Assistant over HTTPS (its internal_url, through
  # Caddy), signed by the profile's own CA. mpv verifies no certificate unless
  # told to, and LVA passes it no TLS options: with LVA_TLS_CA_FILE set, the
  # player verifies against that CA.
  postPatch = ''
    substituteInPlace linux_voice_assistant/player/libmpv.py \
      --replace-fail 'import mpv
    ' 'import mpv
    import os


    def _lanbat_tls():
        ca_file = os.environ.get("LVA_TLS_CA_FILE")
        return {"tls_verify": "yes", "tls_ca_file": ca_file} if ca_file else {}
    ' \
      --replace-fail 'audio_display=False,' 'audio_display=False, **_lanbat_tls(),'
  '';

  # The program looks for its wake word models, sounds and version.txt next to
  # the package (the repository root, site-packages here).
  postInstall = ''
    site=$out/${python3Packages.python.sitePackages}
    cp -r wakewords sounds $site/
    cp version.txt $site/
  '';

  # libmpv, for python-mpv.
  makeWrapperArgs = [
    "--prefix"
    "LD_LIBRARY_PATH"
    ":"
    "${lib.makeLibraryPath [ mpv-unwrapped ]}"
  ];

  doCheck = false;

  meta = {
    description = "Voice satellite for Home Assistant using the ESPHome protocol";
    homepage = "https://github.com/OHF-Voice/linux-voice-assistant";
    license = lib.licenses.asl20;
    mainProgram = "linux-voice-assistant";
  };
}
