# tests/qbittorrent-settings.nix
#
# lanbat.services.qbittorrent.settings renders categories.json and
# qBittorrent.conf, which podman-qbittorrent writes before every start.
#
# Pure evaluation covers the typing and the assertions. The build then runs the
# real pre-start script against a scratch directory (its /var/lib path and the
# qbt owner swapped out) to see what it leaves: the declared settings, none of
# what the web UI changed, and qBittorrent's own [Meta] section as it was.
{ lib, pkgs }:

let
  evalQbittorrent =
    settings:
    (lib.nixosSystem {
      modules = [
        ../modules/core/settings.nix
        ../modules/core/services.nix
        ../modules/wiring/accounts.nix
        ../services/qbittorrent.nix
        {
          boot.isContainer = true;
          nixpkgs.hostPlatform = "x86_64-linux";
          system.stateVersion = "25.11";
          users.groups.media.gid = 988;
          lanbat.deployment = {
            timezone = "UTC";
            serverIp = "192.0.2.10";
          };
          lanbat.services.qbittorrent.settings = settings;
        }
      ];
    }).config;

  failedAssertions = config: map (a: a.message) (lib.filter (a: !a.assertion) config.assertions);

  rejects =
    config:
    !(builtins.tryEval (builtins.deepSeq config.lanbat.services.qbittorrent.settings true)).success;

  declared = {
    categories = {
      "Music" = "";
      "Music/Albums" = "/media/b/music/albums";
      "Video" = "/media/b/misc/video";
      "Video/TV Shows" = "/media/a/tv/misc";
      "Video/TV Shows/Renovation" = "/media/a/tv/renovation";
    };
    preferences = {
      BitTorrent = {
        "Session\\MaxActiveDownloads" = 8;
        "Session\\QueueingSystemEnabled" = true;
      };
    };
  };

  valid = evalQbittorrent declared;
  prefs = valid.lanbat.services.qbittorrent.settings.preferences;

  script =
    let
      pre = lib.elemAt valid.systemd.services.podman-qbittorrent.serviceConfig.ExecStartPre 1;
    in
    lib.removePrefix "+" (builtins.toString pre);

  checks = {
    "valid settings pass assertions" = failedAssertions valid == [ ];
    "no categories by default" =
      (evalQbittorrent { }).lanbat.services.qbittorrent.settings.categories == { };
    "the web UI listens on loopback only" = prefs.WebUI.Address == "127.0.0.1";
    "the web UI answers the server's address without a login" =
      lib.hasInfix "192.0.2.10/32" prefs.WebUI."AuthSubnetWhitelist"
      && prefs.WebUI.AuthSubnetWhitelistEnabled;
    "the profile's keys sit beside the module's" =
      prefs.BitTorrent."Session\\MaxActiveDownloads" == 8
      && prefs.BitTorrent."Session\\DefaultSavePath" == "/media/b/misc/";
    "a profile changes a default" =
      (evalQbittorrent {
        preferences.BitTorrent."Session\\DefaultSavePath" = "/media/a/misc/";
      }).lanbat.services.qbittorrent.settings.preferences.BitTorrent."Session\\DefaultSavePath"
      == "/media/a/misc/";
    "a profile cannot open the web UI" = rejects (evalQbittorrent {
      preferences.WebUI.Address = "0.0.0.0";
    });
    "a profile cannot turn the login back on" = rejects (evalQbittorrent {
      preferences.WebUI.AuthSubnetWhitelistEnabled = false;
    });
    "a subcategory without its parent fails" = lib.any (lib.hasInfix "needs its parent") (
      failedAssertions (evalQbittorrent {
        categories."Music/Albums" = "/media/b/music/albums";
      })
    );
    "Meta is qBittorrent's" = lib.any (lib.hasInfix "Meta") (
      failedAssertions (evalQbittorrent {
        preferences.Meta.MigrationVersion = 1;
      })
    );
    "a relative save path is rejected" = rejects (evalQbittorrent {
      categories.Music = "music";
    });
  };

  failed = lib.attrNames (lib.filterAttrs (_: ok: !ok) checks);
in
if failed != [ ] then
  throw "qbittorrent-settings: failed: ${lib.concatStringsSep "; " failed}"
else
  pkgs.runCommand "qbittorrent-settings"
    {
      nativeBuildInputs = [
        pkgs.jq
        pkgs.gnugrep
      ];
      inherit script;
    }
    ''
      dir=$PWD/state/qBittorrent
      mkdir -p "$dir"

      # The script, aimed at a scratch directory and run as the build user.
      sed -e "s|/var/lib/qbittorrent|$PWD/state|" -e 's| -o qbt -g qbt||' "$script" > run.sh
      chmod +x run.sh

      # First start: categories only, no conf to rewrite.
      ./run.sh
      test -f "$dir/categories.json"
      test ! -e "$dir/qBittorrent.conf"

      # A later start, after qBittorrent and the web UI changed things.
      cat > "$dir/qBittorrent.conf" <<'EOF'
      [BitTorrent]
      Session\MaxActiveDownloads=99
      Session\SomethingFromTheUi=1

      [Meta]
      MigrationVersion=8

      [Preferences]
      WebUI\Address=0.0.0.0

      [WebUI]
      Address=0.0.0.0
      AuthSubnetWhitelistEnabled=false
      Password_PBKDF2="@ByteArray(AAAA:BBBB)"
      EOF
      ./run.sh

      conf="$dir/qBittorrent.conf"
      want() { grep -qxF -- "$1" "$conf" || { echo "missing from qBittorrent.conf: $1"; cat "$conf"; exit 1; }; }
      unwanted() { ! grep -qF -- "$1" "$conf" || { echo "kept in qBittorrent.conf: $1"; cat "$conf"; exit 1; }; }

      want '[BitTorrent]'
      want 'Session\MaxActiveDownloads=8'
      want 'Session\QueueingSystemEnabled=true'
      want '[WebUI]'
      want 'Address=127.0.0.1'
      want 'AuthSubnetWhitelistEnabled=true'
      want 'Session\DefaultSavePath=/media/b/misc/'
      want '[Meta]'
      want 'MigrationVersion=8'
      unwanted 'MaxActiveDownloads=99'
      unwanted 'SomethingFromTheUi'
      unwanted '@ByteArray'
      unwanted '0.0.0.0'
      test "$(grep -c '^\[Meta\]' "$conf")" = 1

      # Running again changes nothing.
      cp "$conf" before
      ./run.sh
      cmp before "$conf"

      cats="$dir/categories.json"
      test "$(jq -r 'keys | length' "$cats")" = 5
      test "$(jq -r '."Music/Albums".save_path' "$cats")" = /media/b/music/albums
      test "$(jq -r '.Music.save_path' "$cats")" = ""
      test "$(jq -r '."Video/TV Shows/Renovation".save_path' "$cats")" = /media/a/tv/renovation

      echo ${lib.escapeShellArg (lib.concatStringsSep "\n" (lib.attrNames checks))} > $out
    ''
