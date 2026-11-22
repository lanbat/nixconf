# services/redis.nix
#
# Shared Redis instance; services use separate DB numbers, each claimed in
# lanbat.redis.databases (modules/core/database.nix), which rejects two
# consumers claiming the same one:
#   DB 0 — Authentik  (sessions, cache)
#   DB 1 — Immich     (job queues, cache)
#   DB 2 — RomM       (task queues, cache)
#   DB 3 — Paperless (task queue, cache)
# Nothing is persisted (save = []): every consumer treats Redis as a cache.
{
  lanbat.services.redis.extraPorts = [ 6379 ];

  services.redis.servers.shared = {
    enable = true;
    port = 6379;
    bind = "127.0.0.1";
    save = [ ];
  };
}
