"""Root-only staged deployment; tests first, backup/rollback, no device actions."""
import argparse
from datetime import datetime, timezone
import grp
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import uuid

BRIDGE_FILES = ('actions.py','alexa_direct.py','bridge.py','control_state.py',
                'dispatch.py','hue_local.py','link_hue.py','verify_hue.py','set_hue_target.py','ssh_receive.py',
                'test_bridge.py','test_local_controls.py','docs/CONTROLS.md','docs/ACTIONS.md')
SCALE_FILES = ('reactions.py','reactions.json','sheets_logger.py','test_reactions.py',
               'test_sheets_logger.py','ROAST-SOURCES.md','REACTIONS.md')


def command(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def install(stage):
    if os.geteuid() != 0:
        raise RuntimeError('Root required')
    stage=stage.resolve()
    bridge=stage/'notification-bridge';scale=stage/'renpho-scale'
    environment={**os.environ,'PYTHONPATH':str(bridge)+':'+str(scale)}
    command('/opt/notification-bridge/venv/bin/python','-m','unittest',
            'test_bridge','test_local_controls','-q',cwd=bridge,env=environment)
    command('/opt/renpho-scale/venv/bin/python','-m','unittest',
            'test_reactions','test_sheets_logger','-q',cwd=scale,env=environment)
    if not (stage/'renpho-scale/reactions.json').is_file():
        raise RuntimeError('Missing reaction catalog')
    import json
    if json.loads((scale/'reactions.json').read_text())['special']['enabled']:
        raise RuntimeError('Special scene must remain paused during this deployment')
    backup=Path('/root')/('homelab-controls-before-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    backup.mkdir(mode=0o700)
    targets={}
    for folder,names in ((bridge,BRIDGE_FILES),(scale,SCALE_FILES)):
        for name in names:
            targets[Path('/opt')/folder.name/name]=folder/name
    targets[Path('/etc/renpho-scale/reactions.json')]=scale/'reactions.json'
    dropins={
        Path('/etc/systemd/system/notification-bridge.service.d/local-controls.conf'):
            '[Service]\nSupplementaryGroups=homelab-controls\nReadWritePaths=/var/lib/homelab-controls\n',
        Path('/etc/systemd/system/renpho-sheets.service.d/local-controls.conf'):
            '[Service]\nSupplementaryGroups=bluetooth homelab-controls\n'}
    snapshots={}
    for i,target in enumerate(list(targets)+list(dropins)):
        snapshot=backup/str(i)
        if target.exists():
            stat=target.stat()
            shutil.copy2(target,snapshot)
            os.chown(snapshot,stat.st_uid,stat.st_gid)
        snapshots[target]=snapshot if snapshot.exists() else None
    # Save exact destination mapping without private file contents in output.
    (backup/'targets.json').write_text(json.dumps({str(k):str(v) if v else None for k,v in snapshots.items()}))
    command('groupadd','-f','homelab-controls')
    command('install','-d','-o','notification-bridge','-g','homelab-controls','-m','2750','/var/lib/homelab-controls')
    bridge_gid=grp.getgrnam('notification-bridge').gr_gid
    for target in (Path('/etc/notification-bridge-hue'),):
        target.mkdir(exist_ok=True);os.chown(target,0,bridge_gid);target.chmod(0o750)
    command('systemctl','stop','renpho-sheets','notification-bridge')
    try:
        for target,source in targets.items():
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
            gid=grp.getgrnam('renpho-scale').gr_gid if str(target).startswith('/etc/renpho-scale/') else 0
            os.chown(target,0,gid);target.chmod(0o640 if gid else 0o644)
        for target,body in dropins.items():
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(body);target.chmod(0o644)
        mode=Path('/var/lib/homelab-controls/modes.json')
        if not mode.exists():
            mode.write_text('{"version":1,"meme":true}\n')
            os.chown(mode,pwd.getpwnam('notification-bridge').pw_uid,grp.getgrnam('homelab-controls').gr_gid)
            mode.chmod(0o640)
        command('systemctl','daemon-reload')
        command('systemctl','start','notification-bridge','renpho-sheets')
        command('systemctl','is-active','notification-bridge','renpho-sheets')
    except BaseException:
        for target,snapshot in snapshots.items():
            if snapshot is None:target.unlink(missing_ok=True)
            else:
                shutil.copy2(snapshot,target)
                stat=snapshot.stat();os.chown(target,stat.st_uid,stat.st_gid)
        command('systemctl','daemon-reload')
        command('systemctl','start','notification-bridge','renpho-sheets')
        raise
    print('Installed local controls and replacement jokes. Meme mode defaults on; existing setting preserved.')
    print('Hue linking is still required; no lighting or audible test was performed.')
    print('Root-only rollback backup: '+str(backup))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',type=Path)
    args=parser.parse_args()
    install(args.stage)
