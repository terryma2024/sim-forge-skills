"""Inspect local dependencies and fetch a pinned upstream SimForge source."""
import hashlib
import io
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from common import blender_executable, credential, read_json, sha256, write_json


def doctor(run,c):
    info={'python':sys.version.split()[0],'platform':platform.system(),
          'ffmpeg':shutil.which('ffmpeg'),'ssh':shutil.which('ssh'),'scp':shutil.which('scp'),
          'isaac_scope':'remote' if c['isaac']['host'] else 'local',
          'api_credential_present':False,'key_variable':c['seed3d']['key_env']}
    try: credential(c); info['api_credential_present']=True
    except ValueError: pass
    try:
        executable=blender_executable(c)
        info['blender_executable']=executable
        info['blender_version']=subprocess.check_output([executable,'--version'],text=True).splitlines()[0]
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        info['blender_error']=str(error)
    source=c['simforge']['source_dir']
    info['simforge_source_available']=bool(source and (Path(source).expanduser()/'simforge/core/contracts.py').is_file())
    info['isaac_python_configured']=bool(c['isaac']['python'])
    info['native_isaac_supported_platform']=platform.system() in ('Linux','Windows')
    info['status']='inspected'
    write_json(run/'state/environment.json',info)
    return info


def bootstrap(run,c):
    """Fetch/build only upstream SimForge, with a fixed commit and safe extraction.

    Blender, Isaac Sim and MCP installation remain explicit host initialization
    operations, described in references/environment.md. No global preferences are
    changed by this command.
    """
    commit=c['simforge']['commit']
    if len(commit)!=40 or any(x not in '0123456789abcdef' for x in commit):
        raise ValueError('SimForge commit must be a full Git commit SHA')
    runtime=run/'.runtime'; runtime.mkdir(exist_ok=True)
    destination=runtime/'simforge-source'
    marker=destination/'simforge/core/contracts.py'
    if not marker.exists():
        url='https://codeload.github.com/XIAODUOLU/SimForge/tar.gz/'+commit
        with urllib.request.urlopen(url,timeout=60) as response: content=response.read()
        archive=runtime/'simforge-source.tar.gz'; archive.write_bytes(content)
        with tarfile.open(fileobj=io.BytesIO(content),mode='r:gz') as bundle:
            bundle.extractall(runtime/'unpacked',filter='data')
        roots=list((runtime/'unpacked').iterdir())
        if len(roots)!=1 or not (roots[0]/'simforge/core/contracts.py').is_file():
            raise ValueError('Downloaded source does not have the expected SimForge layout')
        roots[0].rename(destination)
        write_json(runtime/'source-lock.json',{'repository':'https://github.com/XIAODUOLU/SimForge',
            'commit':commit,'archive_sha256':hashlib.sha256(content).hexdigest()})
    else:
        lock=read_json(runtime/'source-lock.json')
        if not lock or lock['commit']!=commit:
            raise ValueError('Existing source lacks the matching commit lock; choose a fresh run')
        if sha256(runtime/'simforge-source.tar.gz')!=lock['archive_sha256']:
            raise ValueError('Pinned source archive hash changed; inspect it before continuing')
    subprocess.run([sys.executable,str(destination/'scripts/build.py')],check=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    c['simforge']['source_dir']=str(destination.resolve())
    write_json(run/'config.json',c)
    return {'status':'bootstrapped','simforge_source':str(destination),
            'extension_zip':str(destination/'dist/simforge-1.0.0-extension.zip'),
            'next':'Run doctor. Install the extension ZIP in Blender Preferences if interactive panel use is wanted.'}
