# services/llm-gateway.nix
#
# LiteLLM on the loopback: the assistant router's way to cloud models. Each
# name the router asks for ("smart", "deep") is an ordered list of models;
# LiteLLM tries the next on an error, a timeout or a rate limit. Keys are in
# the llm-gateway-env secret (ANTHROPIC_API_KEY=..., OPENAI_API_KEY=...),
# never in Home Assistant. budgetMonthly (US dollars) is LiteLLM's own
# counter: without a database it lives in memory and starts again when the
# gateway restarts, so set a hard spend limit in the provider's console too.
# There is no master key: any process on the server can use the gateway,
# which listens on the loopback only.
#
# Settings: lanbat.services.llm-gateway.settings.models and budgetMonthly,
# for example a second provider behind Claude:
#
#   lanbat.services.llm-gateway.settings.models.smart = [
#     { model = "anthropic/claude-haiku-4-5"; }
#     { model = "openai/gpt-4.1-mini"; }
#   ];
#
# or Google's free tier (GEMINI_API_KEY=... in the secret), quick with
# thinking off:
#
#   lanbat.services.llm-gateway.settings.models = {
#     smart = [
#       { model = "gemini/gemini-3.5-flash"; params.reasoning_effort = "none"; }
#       { model = "gemini/gemini-3.1-flash-lite"; }
#     ];
#     deep = [ { model = "gemini/gemini-3.8-flash"; } ];
#   };
#
# Always-on with the router; loopback only.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkOption types;
  hostLib = import ../lib/host.nix { inherit lib; };
  enabled = hostLib.haLlmIsRouter (config.lanbat.deployment.haLlm or null);
  cfg = config.lanbat.services.llm-gateway.settings;
  port = 8093;
  gatewaySettings.options = {
    models = mkOption {
      type = types.attrsOf (
        types.listOf (
          types.submodule {
            options.model = mkOption {
              type = types.str;
              example = "anthropic/claude-haiku-4-5";
              description = "A LiteLLM model name: provider/model.";
            };
            options.params = mkOption {
              type = types.attrsOf types.anything;
              default = { };
              example = {
                reasoning_effort = "none";
              };
              description = ''
                More LiteLLM parameters for this model (litellm_params), such as
                reasoning_effort = "none" to keep a thinking model quick enough
                for a spoken reply.
              '';
            };
          }
        )
      );
      default = {
        smart = [ { model = "anthropic/claude-haiku-4-5"; } ];
        deep = [ { model = "anthropic/claude-sonnet-5-5"; } ];
      };
      description = "Names the router asks for, each an ordered list of models (the first that answers wins).";
    };
    budgetMonthly = mkOption {
      type = types.ints.positive;
      default = 10;
      description = ''
        Spending limit per month, in US dollars; beyond it requests fail and the
        router says it can't reach the online assistant. LiteLLM counts in
        memory, so a restart of the gateway starts the count again: set a hard
        limit in the provider's console as well.
      '';
    };
  };
  # Each chain as LiteLLM deployments: "smart", "smart-fallback-1", ...
  modelList = lib.concatLists (
    lib.mapAttrsToList (
      name: chain:
      lib.imap0 (i: m: {
        model_name = if i == 0 then name else "${name}-fallback-${toString i}";
        litellm_params = m.params // {
          inherit (m) model;
        };
      }) chain
    ) cfg.models
  );
  # LiteLLM walks one fallback list only: a fallback that itself fails does not
  # fall back again unless it has its own entry. So each name in a chain falls
  # back to every name after it, and the last one to nothing. Without this, two
  # Gemini models down in a row ended the chain even with Groq behind them.
  fallbacks = lib.concatLists (
    lib.mapAttrsToList (
      name: chain:
      let
        names = [ name ] ++ lib.genList (i: "${name}-fallback-${toString (i + 1)}") (lib.length chain - 1);
      in
      lib.imap0 (i: n: { ${n} = lib.drop (i + 1) names; }) names
    ) cfg.models
  );
in
{
  config = lib.mkMerge [
    { lanbat.settingsSchema.llm-gateway = gatewaySettings; }
    (lib.mkIf enabled {
      lanbat.services.llm-gateway = {
        inherit port;
        # LiteLLM runs as a dynamic user, so there is no llm-gateway account to
        # own the file; systemd reads the EnvironmentFile as root.
        secrets.llm-gateway-env.owner = "root";
      };
      services.litellm = {
        enable = true;
        host = "127.0.0.1";
        inherit port;
        environmentFile = config.lanbat.secrets.llm-gateway-env.path;
        settings = {
          model_list = modelList;
          router_settings = {
            inherit fallbacks;
            # A model out of quota (429 on a free tier) is not retried: the
            # next one in the chain answers at once, and the spent model is
            # left alone for ten minutes. Retrying cost 15 s and more.
            num_retries = 0;
            allowed_fails = 0;
            cooldown_time = 600;
            timeout = 15;
          };
          litellm_settings = {
            max_budget = cfg.budgetMonthly;
            budget_duration = "30d";
            drop_params = true;
          };
        };
      };
      # A stable name for the router to order after, whatever the module calls
      # its unit.
      systemd.services.llm-gateway = {
        description = "LLM gateway (LiteLLM)";
        wantedBy = [ "multi-user.target" ];
        bindsTo = [ "litellm.service" ];
        after = [ "litellm.service" ];
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          ExecStart = "${pkgs.coreutils}/bin/true";
        };
      };
    })
  ];
}
