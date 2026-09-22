"""Build a deterministic, allowlisted customer ZIP; never bundle local settings."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from diskpick_version import VERSION
ROOT=Path(__file__).resolve().parent
FILES=['diskboard.py','diskboard_update.py','Start DiskBoard.command','UPDATES.md','diskpick.py','diskpick_cli.py','diskpick_catalog.py','diskpick_discovery.py','diskpick_engine.py',
       'diskpick_panes.py','diskpick_report.py','diskpick_report_ui.py','diskpick_setup.py','diskpick_tui.py',
       'diskpick_ui.py','diskpick_worktrees.py','diskpick_version.py','areas.json','install.sh','Start diskpick.command',
       'report.png','result.png','panes.png','START-HERE.md','README.md','REPORTS.md','EXTENDING.md','LICENSE','AGENTS.md']

def build(destination,root=ROOT):
    destination=Path(destination)
    members={}
    for name in FILES:
        source=root/name
        if source.is_symlink() or not source.is_file():raise ValueError('Missing or symlinked release input: '+name)
        data=source.read_bytes()
        if b'/Users/' in data or b'/private/var/folders/' in data:raise ValueError('Private path in release input: '+name)
        members[name]=data
    manifest={'version':VERSION,'platform':'macOS','files':{name:hashlib.sha256(data).hexdigest() for name,data in sorted(members.items())}}
    members['MANIFEST.json']=(json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode()
    with destination.open('xb') as stream:
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for name,data in sorted(members.items()):
                info=zipfile.ZipInfo('diskpick/'+name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                info.create_system=3;info.external_attr=(0o100755 if name.endswith(('.sh','.command')) else 0o100644)<<16
                archive.writestr(info,data)
    return hashlib.sha256(destination.read_bytes()).hexdigest()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);args=parser.parse_args()
    print(build(args.output)+'  '+args.output.name)
