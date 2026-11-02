# services/llama-cpp.nix
#
# A small local language model for Home Assistant's voice conversation agent,
# served by llama.cpp's llama-server on this host's loopback.
#
# Home Assistant (services/home-assistant.nix) points its conversation agent
# at it when lanbat.deployment.haLlm.baseUrl is this service's URL
# (http://127.0.0.1:8091/v1) and model is a name from the table below. Local
# intents are tried first, so only what Home Assistant cannot match itself
# ("turn on the kitchen light") reaches the model.
#
# The service runs only where haLlm is on the loopback: a profile that uses an
# outside API, or none, imports this file and gets nothing from it.
#
# What fits (measured on the i5-8500T server, with Frigate running)
# ------------------------------------------------------------------
# The server has six cores, no discrete GPU and little spare memory, so the
# model must be small, the prompt short and its start cached:
#
#   model        decode      prompt processing   warm request (cached start)
#   qwen3-1.7b   ~10-15 t/s  ~50 t/s             2-5 s per model round
#   qwen3-4b     ~5-8 t/s    ~20 t/s             10-20 s per model round
#
# Prompt processing is the cost that matters: a 900-token prompt takes about
# 20 s on qwen3-1.7b and 40 s on qwen3-4b the first time. llama-server keeps
# the previous request's tokens (one slot, --cache-reuse), so Home Assistant's
# prompt must open with text that does not change between requests. The
# conversation agent's prompt is built that way in home-assistant-post-setup:
# instructions and the entity list first, no clock and no device states, which
# the model reads with a tool instead. The first request after a start, or
# after the exposed entities change, pays the full prompt once.
#
# The binary
# ----------
# nixpkgs builds llama.cpp for baseline x86-64: no AVX2, so token generation
# ran 5-10 times slower here than with it (1.2 against 8 t/s on one model),
# and the BLAS path is slower and spins threads. The package below is the
# same one rebuilt with AVX2/FMA/F16C/BMI2 (all of which the server's CPU has)
# and without BLAS. It compiles on first deploy.
#
# Resource limits
# ---------------
# The server also runs Frigate (about two cores, one of them licence plate
# recognition), so the unit is capped and runs at lower priority: three
# threads for generation, four for prompt processing, a share of CPU below the
# default, 400% at most, a memory ceiling with no swap (swap is already full
# on the server), and the kernel's OOM killer picks it first. The weights are
# deliberately NOT mlocked: mlocking pinned ~2.4 GiB the host could never
# reclaim, which drove the 2026-11-01 freeze -- the global OOM stalled
# direct-reclaim and wedged Caddy/Tang/sshd until a power cycle. Keeping the
# weights in the page cache lets the kernel evict them under pressure instead
# of OOM-wedging the box. Measured without any of these on:
# llama-server's threads wait for one another, so a thread descheduled by a
# busier service slows all of them; keep the thread count at or below the free
# cores rather than lowering the priority further.
#
# Settings
# --------
# lanbat.services.llama-cpp.settings (options below): the model, the threads
# and the context size. A profile sets them from a module in the host's
# modules (docs/extensibility.md#service-settings):
#
#   lanbat.services.llama-cpp.settings.model = "qwen3-4b";
#
# Always-on: yes. State is the pinned model in the Nix store; no secrets.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  inherit (lib) mkOption types;

  hostLib = import ../lib/host.nix { inherit lib; };

  # Read with `or` because a host assembled without the settings module, as
  # the pure-eval tests do, has no deployment to ask.
  enabled = hostLib.haLlmIsLocal (config.lanbat.deployment.haLlm or null);
  behindRouter = hostLib.haLlmIsRouter (config.lanbat.deployment.haLlm or null);

  cfg = config.lanbat.services.llama-cpp.settings;

  port = config.lanbat.services.llama-cpp.port;

  # Models that fit the server, by name. memoryMax is the weights plus the KV
  # cache for contextSize 4096 and the compute buffers, with some headroom.
  models = {
    "qwen3-1.7b" = {
      file = "Qwen3-1.7B-Q4_K_M.gguf";
      url = "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf";
      sha256 = "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897";
      memoryMax = "3G";
    };
    "qwen3-4b" = {
      file = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf";
      url = "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf";
      sha256 = "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597";
      memoryMax = "5G";
    };
  };

  model = models.${cfg.model};

  modelFile = pkgs.fetchurl {
    inherit (model) url sha256;
    name = model.file;
  };

  # llama.cpp with the instruction sets the server's CPU has; see "The binary".
  llamaCpp = (pkgs.llama-cpp.override { blasSupport = false; }).overrideAttrs (old: {
    cmakeFlags = old.cmakeFlags ++ [
      "-DGGML_AVX=ON"
      "-DGGML_AVX2=ON"
      "-DGGML_FMA=ON"
      "-DGGML_F16C=ON"
      "-DGGML_BMI2=ON"
    ];
  });

  llamaCppSettings = {
    options = {
      model = mkOption {
        type = types.enum (lib.attrNames models);
        default = "qwen3-1.7b";
        example = "qwen3-4b";
        description = ''
          The model, Q4_K_M quantised and pinned by hash. qwen3-1.7b answers a
          cached request in a few seconds but follows tool instructions less
          reliably; qwen3-4b is more capable and several times slower on this
          CPU (see the table at the top of services/llama-cpp.nix). It is also
          the model name Home Assistant sends, so set lanbat.deployment.haLlm.model
          to the same name.
        '';
      };
      threads = mkOption {
        type = types.ints.positive;
        default = 3;
        description = ''
          Threads for token generation. Generation is limited by memory
          bandwidth, so more than three barely helps, and threads beyond the
          cores Frigate leaves free slow every thread down.
        '';
      };
      batchThreads = mkOption {
        type = types.ints.positive;
        default = 4;
        description = "Threads for prompt processing, which is limited by compute rather than bandwidth.";
      };
      contextSize = mkOption {
        type = types.ints.between 1024 32768;
        default = 4096;
        description = ''
          Tokens of prompt and reply the server holds. The KV cache grows with
          it (about 0.1 MB per token for qwen3-1.7b), inside the unit's memory
          ceiling; Home Assistant's prompt must fit, so keep the list of
          exposed entities short.
        '';
      };
    };
  };
in
{
  config = lib.mkMerge [
    { lanbat.settingsSchema.llama-cpp = llamaCppSettings; }
    (lib.mkIf enabled {
      assertions = [
        {
          # Behind the router, Home Assistant asks for "assistant" and the
          # router asks llama.cpp for this model by name.
          assertion =
            hostLib.haLlmIsRouter config.lanbat.deployment.haLlm
            || config.lanbat.deployment.haLlm.model == cfg.model;
          message =
            "lanbat: lanbat.deployment.haLlm.model is \"${config.lanbat.deployment.haLlm.model}\", but the llama-cpp"
            + " service serves \"${cfg.model}\" (lanbat.services.llama-cpp.settings.model); set both to the same name.";
        }
      ];

      # Loopback only, so there is no vhost, no firewall port and no policy.
      lanbat.services.llama-cpp.port = 8091;

      services.llama-cpp = {
        enable = true;
        package = llamaCpp;
        settings = {
          model = "${modelFile}";
          host = "127.0.0.1";
          inherit port;
          alias = cfg.model;
          threads = cfg.threads;
          threads-batch = cfg.batchThreads;
          ctx-size = cfg.contextSize;
          # One slot: the previous request's tokens stay cached for the next one.
          parallel = 1;
          cache-reuse = 256;
          # The model's own chat template, which carries its tool-call format.
          jinja = true;
          # qwen3-1.7b would otherwise think at length before every reply, which
          # costs seconds of generation; the 2507 instruct models ignore this.
          # The backslashes are for systemd: ExecStart is unquoted, so it would
          # otherwise strip the quotes and hand llama.cpp `{enable_thinking:false}`
          # (invalid JSON, so llama-server exits 1).
          chat-template-kwargs = ''{\"enable_thinking\":false}'';
          no-webui = true;
          # No --mlock: see "Resource limits" -- the weights must stay in the
          # page cache so the kernel can evict them under memory pressure.
        };
      };

      systemd.services.llama-cpp.serviceConfig = {
        # Called by Home Assistant's agent, the model waits its turn behind
        # Frigate and the rest. Behind the assistant router it is on the voice
        # path, where a slow answer is worse: there it goes first.
        Nice = if behindRouter then -5 else 5;
        CPUWeight = if behindRouter then 200 else 50;
        CPUQuota = "400%";
        MemoryMax = model.memoryMax;
        MemorySwapMax = 0;
        # The first to go if the host runs out of memory.
        OOMScoreAdjust = 500;
        # The module waits five minutes between restarts; a voice assistant
        # that failed once should be back in seconds.
        RestartSec = lib.mkForce 10;
      };
    })
  ];
}
