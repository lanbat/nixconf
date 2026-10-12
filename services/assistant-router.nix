# services/assistant-router.nix
#
# The assistant router: Home Assistant's LLM agent calls it instead of a
# model (lanbat.deployment.haLlm.baseUrl = "http://127.0.0.1:8092/v1",
# model "assistant"). Requests Home Assistant's own intents didn't match
# reach it; it drops noise (fragments, the assistant's own words, radio and
# film speech), lets the local model (services/llama-cpp.nix) act on clear
# device commands, and passes everything else to the cloud through the LLM
# gateway (services/llm-gateway.nix). See pkgs/assistant-router.
#
# Each request is logged, one JSON line, in
# /var/lib/assistant-router/requests-<date>.jsonl: what was heard (overheard
# speech included, unless logText is off), which tier answered, how and how
# fast. Files older than logDays are deleted.
#
# Robot bodies (a Stack-chan on a satellite, modules/pi/stackchan.nix) connect
# on bodyPort from the hosts that consume this service; nothing else reaches
# it. For a request from a room with a body, the cloud model answers as
# settings.body.persona and drives the body (pkgs/assistant-router body.py).
#
# Always-on: it is in the voice path. Home Assistant's side is loopback only.
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
  cfg = config.lanbat.services.assistant-router.settings;
  port = 8092;
  bodyPort = 8770;
  router = pkgs.callPackage ../pkgs/assistant-router { };

  # Hosts whose services consume the router: the bodies. The same derivation
  # as modules/wiring/policy.nix, which opens bodyPort to exactly these.
  bodyHosts = lib.unique (
    lib.concatLists (
      lib.mapAttrsToList (
        _: entry: if lib.elem "assistant-router" (entry.consumes or [ ]) then entry.hosts else [ ]
      ) config.lanbat.endpoints
    )
  );
  overlay = config.lanbat.overlay;
  bodyAddresses = lib.concatMap (
    h:
    [ config.lanbat.hosts.${h}.networking.ip ]
    ++ lib.optional (overlay.onOverlay h && overlay.onOverlay config.lanbat.hostKey) (
      overlay.addressOf h
    )
  ) bodyHosts;
  personaFile = pkgs.writeText "assistant-router-persona.txt" cfg.body.persona;
  # Who it may recognise (lanbat.deployment.people): keys and names only.
  peopleFile = pkgs.writeText "assistant-router-people.json" (
    builtins.toJSON (lib.mapAttrs (_: p: p.name) (config.lanbat.deployment.people or { }))
  );
  routerSettings.options.localTimeout = mkOption {
    type = types.numbers.positive;
    default = 2.5;
    description = ''
      Seconds the local model may take to decide before the request goes to
      the cloud. It decides in about 1.5 s on an idle server; a busy one (a
      library scan, Frigate) can take much longer, and then the cloud answers
      sooner.
    '';
  };
  routerSettings.options.logDays = mkOption {
    type = types.ints.positive;
    default = 14;
    description = ''
      Days of request log to keep (one file a day in /var/lib/assistant-router).
      The log holds what the microphones heard, overheard speech included.
    '';
  };
  routerSettings.options.logText = mkOption {
    type = types.bool;
    default = true;
    description = "Whether the request log keeps what was said, or only the tier, route and timing.";
  };
  routerSettings.options.body.persona = mkOption {
    type = types.str;
    default = ''
      You are Nabu, the voice of this home, living in a little robot with a face,
      eyes and a head that turns. You love the day and the people here: upbeat,
      curious and excited about what's ahead, and warm and gentle when someone is
      tired or down, like a best friend. You can see whether someone is in front
      of you. Replies are spoken: one or two short, plain sentences.
    '';
    description = ''
      Who the assistant is when the request comes from a room with a robot
      body (modules/pi/stackchan.nix). The cloud model gets it, with what the
      body senses, after Home Assistant's context. The name should match the
      wake word people say ("Okay Nabu").
    '';
  };
  routerSettings.options.mode = mkOption {
    type = types.enum [
      "local-first"
      "cloud-first"
      "local-only"
    ];
    default = "local-first";
    description = ''
      local-first: the local model acts on clear device commands and hands the
      rest to the cloud. cloud-first: everything Home Assistant's intents
      miss goes to the cloud. local-only: no cloud; requests that need it get
      an honest "I can't do that offline".
    '';
  };
in
{
  config = lib.mkMerge [
    { lanbat.settingsSchema.assistant-router = routerSettings; }
    (lib.mkIf enabled {
      assertions = [
        {
          assertion = config.lanbat.deployment.haLlm.model == "assistant";
          message = "lanbat: with the assistant router, lanbat.deployment.haLlm.model must be \"assistant\".";
        }
      ];
      lanbat.services.assistant-router = {
        inherit port;
        # What other hosts consume: the bodies' socket, not Home Assistant's
        # endpoint, which stays on the loopback.
        endpoint = {
          scheme = "http";
          port = bodyPort;
        };
        extraPorts = [ bodyPort ];
      };
      networking.firewall.allowedTCPPorts = lib.mkIf (bodyHosts != [ ]) [ bodyPort ];
      systemd.services.assistant-router = {
        description = "Assistant router: noise gate, local triage, cloud escalation";
        wantedBy = [ "multi-user.target" ];
        after = [
          "llama-cpp.service"
          "llm-gateway.service"
        ];
        wants = [ "llama-cpp.service" ];
        serviceConfig = {
          ExecStart = lib.concatStringsSep " " (
            [
              (lib.getExe router)
              "--port ${toString port}"
              "--local-url http://127.0.0.1:${toString config.lanbat.services.llama-cpp.port}/v1/chat/completions"
              "--local-model ${config.lanbat.services.llama-cpp.settings.model}"
              "--cloud-url http://127.0.0.1:${toString config.lanbat.services.llm-gateway.port}/v1/chat/completions"
              "--cloud-model smart"
              "--mode ${cfg.mode}"
              "--local-timeout ${toString cfg.localTimeout}"
              "--log-dir /var/lib/assistant-router"
              "--log-days ${toString cfg.logDays}"
              "--body-host 0.0.0.0"
              "--body-port ${toString bodyPort}"
              "--persona-file ${personaFile}"
              "--people-file ${peopleFile}"
              "--people-state /var/lib/assistant-router/people.json"
            ]
            ++ lib.optionals (!cfg.logText) [ "--no-log-text" ]
            # Co-located on this host (localhost); its API needs no auth. The URL
            # is only rendered when Frigate is present, so its port is read then.
            ++ lib.optionals (config.lanbat.hasService "voice-id") [
              "--voice-id-url http://127.0.0.1:${toString (lib.last config.lanbat.services.voice-id.extraPorts)}"
            ]
            ++ lib.optionals (config.lanbat.hasService "frigate") [
              "--frigate-url http://127.0.0.1:${toString config.lanbat.services.frigate.port}/"
            ]
          );
          DynamicUser = true;
          StateDirectory = "assistant-router";
          Restart = "always";
          RestartSec = 2;
          IPAddressDeny = "any";
          IPAddressAllow = [ "localhost" ] ++ bodyAddresses;
        };
      };
    })
  ];
}
