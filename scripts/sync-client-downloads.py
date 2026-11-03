#!/usr/bin/env python3
"""Install successful GitHub CI client artifacts into the robot download directory."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

REPO = 'cdibona/PiBotPal'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', help='Successful Native clients workflow run ID; defaults to latest successful run')
args = parser.parse_args()

def gh(*arguments):
    return subprocess.check_output(['gh', *arguments])

run_id = args.run
if not run_id:
    runs = json.loads(gh('run', 'list', '--repo', REPO, '--workflow', 'clients.yml', '--branch', 'main', '--status', 'success', '--limit', '1', '--json', 'databaseId'))
    if not runs:
        raise SystemExit('No successful client build found')
    run_id = str(runs[0]['databaseId'])
run = json.loads(gh('api', f'repos/{REPO}/actions/runs/{run_id}'))
if run['conclusion'] != 'success' or run['path'] != '.github/workflows/clients.yml' or run['head_branch'] != 'main':
    raise SystemExit('Expected a successful Native clients build from main')
artifacts = json.loads(gh('api', f'repos/{REPO}/actions/runs/{run_id}/artifacts'))['artifacts']
root = Path(os.environ.get('PIBOTPAL_DATA', '~/.local/share/pibotpal')).expanduser() / 'downloads'
root.mkdir(parents=True, exist_ok=True)
manifest = {'run': int(run_id), 'commit': run['head_sha'], 'source': run['html_url'], 'files': {}}
# Exact members only: never extract arbitrary archive paths.
for artifact_name, member, filename in (
    ('PiBotPal-Android-debug', 'app-debug.apk', 'PiBotPal-Android.apk'),
    ('PiBotPal-Windows-x64', 'PiBotPal-Windows-x64.zip', 'PiBotPal-Windows-x64.zip'),
):
    artifact = next((a for a in artifacts if a['name'] == artifact_name and not a['expired']), None)
    if not artifact:
        raise SystemExit(f'Missing unexpired artifact: {artifact_name}')
    archive = gh('api', f'repos/{REPO}/actions/artifacts/{artifact["id"]}/zip')
    with zipfile.ZipFile(io.BytesIO(archive)) as outer:
        data = outer.read(member)
    with tempfile.NamedTemporaryFile(dir=root, delete=False) as output:
        output.write(data)
        temp = Path(output.name)
    temp.chmod(0o644)
    temp.replace(root / filename)
    manifest['files'][filename] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    print(f'Installed {filename}: {len(data):,} bytes')
(root / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(f'Build {run_id} ({run["head_sha"][:7]})')
