# tests/llama-cpp-settings.nix
#
# The local conversation model (services/llama-cpp.nix): it runs only where
# lanbat.deployment.haLlm is on the loopback, serves the model its settings
# name, is held to the limits that keep Frigate's CPU free, and must agree with
# haLlm on the model name. Pure evaluation: the derivation only builds when
# every case holds.
{ lib, pkgs }:

let
  hostLib = import ../lib/host.nix { inherit lib; };

  evalLlm =
    {
      haLlm,
      settings ? null,
    }:
    (lib.nixosSystem {
      modules = [
        ../modules/core/services.nix
        ../modules/wiring/accounts.nix
        ../services/llama-cpp.nix
        {
          # The settings module declares the real option; the service reads
          # only haLlm from it.
          options.lanbat.deployment.haLlm = lib.mkOption {
            type = lib.types.nullOr lib.types.attrs;
            default = null;
          };
          config = lib.mkMerge [
            {
              boot.isContainer = true;
              nixpkgs.hostPlatform = "x86_64-linux";
              system.stateVersion = "25.11";
              lanbat.deployment.haLlm = haLlm;
            }
            (lib.optionalAttrs (settings != null) {
              lanbat.services.llama-cpp.settings = settings;
            })
          ];
        }
      ];
    }).config;

  failedAssertions = config: map (a: a.message) (lib.filter (a: !a.assertion) config.assertions);

  rejects =
    config: !(builtins.tryEval (builtins.deepSeq config.services.llama-cpp.settings true)).success;

  local = model: {
    baseUrl = "http://127.0.0.1:8091/v1";
    inherit model;
  };

  small = evalLlm { haLlm = local "qwen3-1.7b"; };
  large = evalLlm {
    haLlm = local "qwen3-4b";
    settings.model = "qwen3-4b";
  };
  tuned = evalLlm {
    haLlm = local "qwen3-1.7b";
    settings = {
      threads = 2;
      batchThreads = 3;
      contextSize = 2048;
    };
  };
  outside = evalLlm {
    haLlm = {
      baseUrl = "https://llm.test/v1";
      model = "qwen3-1.7b";
    };
  };
  none = evalLlm { haLlm = null; };

  # ExecStart joins every argument with a space: --alias qwen3-1.7b.
  flag = name: value: "${name} ${value}";

  execStart = config: config.systemd.services.llama-cpp.serviceConfig.ExecStart;
  limits = config: config.systemd.services.llama-cpp.serviceConfig;

  checks = {
    "a loopback haLlm runs the service" = small.services.llama-cpp.enable;
    "it listens on the loopback only" =
      small.services.llama-cpp.settings.host == "127.0.0.1"
      && small.services.llama-cpp.settings.port == 8091
      && !small.services.llama-cpp.openFirewall;
    "it has no vhost" = small.lanbat.services.llama-cpp.subdomain == null;
    "an outside API does not" =
      !outside.services.llama-cpp.enable && !(outside.lanbat.services ? llama-cpp);
    "no LLM does not" = !none.services.llama-cpp.enable && !(none.lanbat.services ? llama-cpp);

    "the model is named for the API" = lib.hasInfix (flag "--alias" "qwen3-1.7b") (execStart small);
    "the default threads leave Frigate its cores" =
      lib.hasInfix (flag "--threads" "3") (execStart small)
      && lib.hasInfix (flag "--threads-batch" "4") (execStart small);
    "settings reach the flags" =
      lib.hasInfix (flag "--threads" "2") (execStart tuned)
      && lib.hasInfix (flag "--threads-batch" "3") (execStart tuned)
      && lib.hasInfix (flag "--ctx-size" "2048") (execStart tuned);
    "one cached slot" =
      lib.hasInfix (flag "--parallel" "1") (execStart small)
      && lib.hasInfix "--cache-reuse" (execStart small);
    "thinking is off" = lib.hasInfix "enable_thinking" (execStart small);
    "the template carries the tools" = lib.hasInfix "--jinja" (execStart small);

    "the unit is capped" =
      (limits small).CPUQuota == "400%"
      && (limits small).CPUWeight == 50
      && (limits small).MemorySwapMax == 0
      && (limits small).OOMScoreAdjust == 500;
    "the memory ceiling follows the model" =
      (limits small).MemoryMax == "3G" && (limits large).MemoryMax == "5G";
    "it restarts quickly" = (limits small).RestartSec == 10;
    "the larger model is served" = lib.hasInfix (flag "--alias" "qwen3-4b") (execStart large);

    "matching names pass the assertions" =
      failedAssertions small == [ ] && failedAssertions large == [ ];
    "a different model name in haLlm fails" = lib.any (lib.hasInfix "set both to the same name") (
      failedAssertions (evalLlm {
        haLlm = local "qwen3-4b";
      })
    );
    "an unknown model is rejected" = rejects (evalLlm {
      haLlm = local "gpt-oss";
      settings.model = "gpt-oss";
    });

    "loopback addresses are local" = lib.all (url: hostLib.haLlmIsLocal { baseUrl = url; }) [
      "http://127.0.0.1:8091/v1"
      "http://127.0.0.1/v1"
      "http://localhost:8091/v1"
    ];
    "other addresses are not" =
      !(lib.any (url: hostLib.haLlmIsLocal { baseUrl = url; }) [
        "https://llm.test/v1"
        "http://127.0.0.1.example.com/v1"
        "http://192.168.1.10:8091/v1"
      ])
      && !(hostLib.haLlmIsLocal null);
  };

  failed = lib.attrNames (lib.filterAttrs (_: ok: !ok) checks);
in
if failed != [ ] then
  throw "llama-cpp-settings: failed: ${lib.concatStringsSep "; " failed}"
else
  pkgs.runCommand "llama-cpp-settings" { } ''
    echo ${lib.escapeShellArg (lib.concatStringsSep "\n" (lib.attrNames checks))} > $out
  ''
