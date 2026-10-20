"""The profile's root CA among the certificates an Echo Show trusts.

TECHO5's root filesystem is read-only and has no setting for another CA, and
its daemon reads Alpine's bundle (/etc/ssl/certs/ca-certificates.crt) once,
at its start. So, over SSH as root (TECHO5's own dropbear, keys from the
ssh_keys action), the bridge keeps on the writable /data:

    /data/lanbat/system.pem   the image's bundle, as the slot that booted has it
    /data/lanbat/ca.pem       the profile's root CA (deployment.secrets.caCertificate)
    /data/lanbat/bundle.pem   the two together

and binds bundle.pem over the image's bundle, then restarts the daemon (init
starts it again) so it reads the roots afresh. A bind is gone at the next boot,
so this runs on every connection and changes nothing when the device has it
already: one login. A new root in the profile reaches the device with the next
deploy of the server; Caddy's intermediates and leaf certificates renew under
the root by themselves.
"""
from __future__ import annotations

import asyncio
import base64

SYSTEM_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
DIR = "/data/lanbat"


def script(ca_pem: str, directory: str = DIR, bundle: str = SYSTEM_BUNDLE,
           mountinfo: str = "/proc/self/mountinfo") -> str:
    """The shell the device runs (busybox): prints "unchanged" or "installed"."""
    ca = base64.b64encode(ca_pem.encode()).decode()
    return f"""set -eu
D={directory}
SRC={bundle}
MOUNTS={mountinfo}
mkdir -p "$D"
echo '{ca}' | base64 -d > "$D/ca.pem.new"
if grep -q " $SRC " "$MOUNTS"; then bound=1; else bound=0; fi
# The image's own bundle, read only while nothing is bound over it.
[ "$bound" = 1 ] || cat "$SRC" > "$D/system.pem"
cat "$D/system.pem" "$D/ca.pem.new" > "$D/bundle.pem.new"
if [ "$bound" = 1 ] && cmp -s "$D/bundle.pem.new" "$SRC"; then
  rm -f "$D/ca.pem.new" "$D/bundle.pem.new"
  echo unchanged
  exit 0
fi
[ "$bound" = 0 ] || umount "$SRC"
mv "$D/ca.pem.new" "$D/ca.pem"
mv "$D/bundle.pem.new" "$D/bundle.pem"
mount -o bind "$D/bundle.pem" "$SRC"
# The daemon reads the roots once: init starts it again (inittab respawn).
kill $(pidof techo5) 2>/dev/null || true
echo installed
"""


async def install(host: str, key_file: str, known_hosts: str, ca_pem: str, ssh: str = "ssh",
                  timeout: float = 60.0) -> tuple[bool, str]:
    """(it worked, what the device said or why not)."""
    proc = await asyncio.create_subprocess_exec(
        ssh, "-i", key_file, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", f"UserKnownHostsFile={known_hosts}", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10", f"root@{host}", "sh", "-s",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(script(ca_pem).encode()), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return False, "timed out"
    said = (out or b"").decode(errors="replace").strip().splitlines()
    if proc.returncode == 0 and said and said[-1] in ("installed", "unchanged"):
        return True, said[-1]
    return False, (err or b"").decode(errors="replace").strip()[-200:] or f"exit {proc.returncode}"
