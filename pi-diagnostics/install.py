"""Root installer. Back up touched files; do not reboot, stress, or upgrade the Pi."""
import datetime as dt
import os
from pathlib import Path
import shutil
import subprocess
import sys


def run(*args):
    subprocess.run(args, check=True)


def install(source):
    if os.geteuid() != 0:
        raise SystemExit('Run this installer with sudo.')
    run('/usr/bin/python3', '-I', str(source / 'test_diagnostics.py'))
    # Windows tar can mark staged files 777. The staging directory is root-only;
    # normalize unit permissions before systemd's verification and installation.
    for name in ('pi-diagnostics.service', 'pi-diagnostics.timer'):
        (source / name).chmod(0o644)
    run('systemd-analyze', 'verify', str(source / 'pi-diagnostics.service'),
        str(source / 'pi-diagnostics.timer'))
    run('journalctl', '-k', '-b', '--no-pager', '--lines=0')
    backup = Path('/root/pi-diagnostics-before-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    targets = {Path('/opt/pi-diagnostics') / name: source / name
               for name in ('diagnostics.py', 'snapshot.py', 'report.py')}
    targets.update({Path('/etc/systemd/system') / name: source / name
                    for name in ('pi-diagnostics.service', 'pi-diagnostics.timer')})
    targets[Path('/etc/systemd/journald.conf.d/90-pi-diagnostics.conf')] = source / '90-pi-diagnostics.conf'
    originals = {}
    timer_enabled = subprocess.run(['systemctl', 'is-enabled', 'pi-diagnostics.timer'], capture_output=True).returncode == 0
    timer_active = subprocess.run(['systemctl', 'is-active', 'pi-diagnostics.timer'], capture_output=True).returncode == 0
    for index, target in enumerate(targets):
        if target.is_symlink():
            raise SystemExit('Refusing to replace a symlink: ' + str(target))
        if target.exists():
            item = backup / str(index)
            stat = target.stat()
            shutil.copy2(target, item)
            originals[target] = (item, stat.st_uid, stat.st_gid)
        else:
            originals[target] = None
    try:
        # Missing units are expected on a first install.
        subprocess.run(['systemctl', 'stop', 'pi-diagnostics.timer'], capture_output=True)
        subprocess.run(['systemctl', 'stop', 'pi-diagnostics.service'], capture_output=True)
        for target, origin in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
            os.chown(target, 0, 0)
            target.chmod(0o644)
        state = Path('/var/lib/pi-diagnostics')
        state.mkdir(mode=0o700, exist_ok=True)
        state.chmod(0o700)
        os.chown(state, 0, 0)
        run('systemd-tmpfiles', '--create', '--prefix', '/var/log/journal')
        run('systemctl', 'restart', 'systemd-journald.service')
        run('journalctl', '--flush')
        run('journalctl', '--sync')
        run('systemctl', 'daemon-reload')
        run('systemctl', 'enable', '--now', 'pi-diagnostics.timer')
        run('systemctl', 'start', 'pi-diagnostics.service')
        if not list(state.glob('health-*.jsonl')):
            raise RuntimeError('No health snapshot created.')
        run('systemctl', 'is-active', 'pi-diagnostics.timer')
        print('Installed. Previous files backed up at ' + str(backup))
        print('Low-priority two-minute snapshots and bounded info-level persistent journals are enabled. No reboot requested.')
    except Exception:
        subprocess.run(['systemctl', 'stop', 'pi-diagnostics.timer'])
        subprocess.run(['systemctl', 'stop', 'pi-diagnostics.service'])
        for target, original in originals.items():
            if original:
                saved, uid, gid = original
                shutil.copy2(saved, target)
                os.chown(target, uid, gid)
            else:
                target.unlink(missing_ok=True)
        subprocess.run(['systemctl', 'daemon-reload'])
        subprocess.run(['systemctl', 'restart', 'systemd-journald.service'])
        subprocess.run(['systemctl', 'enable' if timer_enabled else 'disable', 'pi-diagnostics.timer'])
        if timer_active:
            subprocess.run(['systemctl', 'start', 'pi-diagnostics.timer'])
        print('Installation failed; restored touched configuration files. Kept diagnostic records and backup.')
        raise


if __name__ == '__main__':
    install(Path(__file__).resolve().parent)
