import os
import stat
import subprocess

from techo5_bridge.ca import script

CA = "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n"


def device(tmp_path):
    """A stand-in for the device: the image's bundle, mountinfo, and mount,
    umount, pidof and kill that record what they were asked."""
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "bundle.crt").write_text("SYSTEM ROOTS\n")
    (tmp_path / "mountinfo").write_text("1 0 0:1 / / ro - ext4 /dev/root ro\n")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "log"
    stubs = {
        # A bind: the bundle's content shows at the target, and mountinfo lists it.
        "mount": f'echo "mount $*" >> {log}; cp "$3" "$4"; echo "9 1 0:2 / $4 rw - ext4 x rw" >> {tmp_path}/mountinfo',
        "umount": f'echo "umount $*" >> {log}; printf "SYSTEM ROOTS\\n" > "$1"; grep -v " $1 " {tmp_path}/mountinfo > {tmp_path}/m.new || true; mv {tmp_path}/m.new {tmp_path}/mountinfo',
        # The daemon is found and signalled; here pidof finds nothing, so the
        # script's kill (a shell builtin) signals nobody on this machine.
        "pidof": f'echo "pidof $*" >> {log}',
    }
    for name, body in stubs.items():
        f = bin_ / name
        f.write_text("#!/bin/sh\n" + body + "\n")
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}")
    return etc / "bundle.crt", tmp_path / "data", tmp_path / "mountinfo", log, env


def run(ca, bundle, data, mounts, env):
    out = subprocess.run(["sh", "-s"], input=script(ca, str(data), str(bundle), str(mounts)), text=True,
                         capture_output=True, env=env, check=True)
    return out.stdout.strip().splitlines()[-1]


def test_the_ca_joins_the_systems_roots_and_the_daemon_restarts(tmp_path):
    bundle, data, mounts, log, env = device(tmp_path)
    assert run(CA, bundle, data, mounts, env) == "installed"
    assert bundle.read_text() == "SYSTEM ROOTS\n" + CA
    assert (data / "system.pem").read_text() == "SYSTEM ROOTS\n"
    assert "pidof techo5" in log.read_text()


def test_a_device_that_has_it_is_left_alone(tmp_path):
    bundle, data, mounts, log, env = device(tmp_path)
    run(CA, bundle, data, mounts, env)
    log.write_text("")
    assert run(CA, bundle, data, mounts, env) == "unchanged"
    assert log.read_text() == ""  # no mount, no restart


def test_a_new_root_replaces_the_old_one(tmp_path):
    bundle, data, mounts, log, env = device(tmp_path)
    run(CA, bundle, data, mounts, env)
    new = CA.replace("TEST", "NEW")
    assert run(new, bundle, data, mounts, env) == "installed"
    assert bundle.read_text() == "SYSTEM ROOTS\n" + new  # the system's roots and the new one, not the old
    assert "umount" in log.read_text()
