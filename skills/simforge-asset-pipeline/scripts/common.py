"""Run contracts shared by the CLI, Blender and Isaac workers (stdlib only)."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil

DEFAULTS = {
    'schema_version': 1, 'mode': 'checkpoint', 'prompt': '',
    'asset': {'height_m': .09, 'mass_kg': .3, 'require_cavity': False,
              'static_friction': .6, 'dynamic_friction': .5, 'restitution': .05},
    'seed3d': {'model': 'doubao-seed3d-2-0-260328', 'key_env': 'ARK_API_KEY',
               'env_file': '~/.env', 'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
               'subdivision': 'high', 'poll_seconds': 15, 'wait_seconds': 900},
    'blender': {'executable': None, 'gpu_backend': 'AUTO', 'target_faces': 10000,
                'voxel_size': .005, 'resolution': 2048, 'cage_extrusion': .02, 'samples': 32},
    'simforge': {'commit': 'b80ae50f5f6d8ec4353d8374dce972744e5a233a', 'source_dir': None},
    'isaac': {'host': None, 'directory': None, 'python': None, 'upload_authorized': False,
              'authorized_destination': None, 'eula_accepted': False,
              'test': 'drop', 'physics_hz': 120, 'duration_s': 5,
              'ball_angular_damping': 0.05, 'contact_offset_m': .0005,
              'stability_tolerance_m': .001},
}
CHECKPOINTS = ('reference', 'model', 'blender', 'simulation')


def merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def read_json(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else copy.deepcopy(default)


def write_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def config_at(run):
    config = read_json(Path(run) / 'config.json')
    if not config:
        raise ValueError('Run is not initialized; use init first')
    validate_config(config)
    return config


def validate_config(c):
    if c['mode'] not in ('checkpoint', 'end-to-end'):
        raise ValueError('mode must be checkpoint or end-to-end')
    if c['asset']['height_m'] <= 0 or c['asset']['mass_kg'] <= 0:
        raise ValueError('Height and mass must be positive SI values')
    if c['blender']['resolution'] not in (512, 1024, 2048, 4096):
        raise ValueError('Unsupported texture resolution')
    if c['isaac']['test'] not in ('cup-comparison', 'drop'):
        raise ValueError('isaac.test must be cup-comparison or drop')
    if c['isaac']['test'] == 'cup-comparison' and not c['asset']['require_cavity']:
        raise ValueError('Cup comparison requires a verified cavity; use drop for solid assets')
    if c['isaac']['physics_hz'] <= 0 or c['isaac']['duration_s'] <= 0:
        raise ValueError('Physics rate and duration must be positive')
    for k in ('static_friction', 'dynamic_friction', 'restitution'):
        if c['asset'][k] < 0:
            raise ValueError(f'{k} must be non-negative')


def credential(c):
    name = c['seed3d']['key_env']
    value = os.environ.get(name)
    if value:
        return value
    source = Path(c['seed3d']['env_file']).expanduser()
    if source.is_file():
        for line in source.read_text().splitlines():
            match = re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*)', line)
            if match and match[1] == name:
                parts = shlex.split(match[2], comments=True)
                if parts:
                    return parts[0]
    raise ValueError(f'{name} is missing; set the environment variable or configured env_file. Do not paste secrets.')


def blender_executable(c):
    candidate = c['blender']['executable'] or shutil.which('blender')
    if not candidate and Path('/Applications/Blender.app/Contents/MacOS/Blender').is_file():
        candidate = '/Applications/Blender.app/Contents/MacOS/Blender'
    if not candidate:
        raise ValueError('Blender not found; set blender.executable')
    return str(candidate)


def checkpoint_files(run, checkpoint):
    a = Path(run) / 'assets'
    mapping = {
        'reference': [a/'reference.png'],
        'model': [a/'source.glb', a/'source_preview.png', a/'source_report.json'],
        'blender': [a/'asset_pipeline.blend', a/'asset_validation.json', a/'high_low_comparison.png'],
        'simulation': [a/'simulation_result.json', a/'simulation.mp4', a/'simulation_final.png'],
    }
    files = mapping[checkpoint]
    if not all(p.is_file() for p in files):
        raise ValueError(f'Checkpoint {checkpoint} is missing an artifact')
    return files


def approve(run, checkpoint):
    files = checkpoint_files(run, checkpoint)
    approvals = read_json(Path(run)/'state/approvals.json', {})
    approvals[checkpoint] = {str(p.relative_to(run)): sha256(p) for p in files}
    write_json(Path(run)/'state/approvals.json', approvals)


def approval_valid(run, checkpoint):
    try:
        files = checkpoint_files(run, checkpoint)
    except ValueError:
        return False
    approval = read_json(Path(run)/'state/approvals.json', {}).get(checkpoint, {})
    return approval == {str(p.relative_to(run)): sha256(p) for p in files}


def require_remote_authorization(c):
    i = c['isaac']
    if not i.get('upload_authorized'):
        raise ValueError('Remote upload authorization is required for the configured destination')
    if i.get('authorized_destination') != {'host': i.get('host'), 'directory': i.get('directory')}:
        raise ValueError('Remote destination differs from the authorized destination')
    host, directory = i.get('host'), i.get('directory')
    if not host or not re.fullmatch(r'[A-Za-z0-9_.@-]+', host) or host.startswith('-'):
        raise ValueError('Invalid SSH host')
    if not directory or not directory.startswith('/') or not re.fullmatch(r'/[A-Za-z0-9_./-]+', directory) or '..' in Path(directory).parts:
        raise ValueError('Remote directory must be an absolute path without shell metacharacters')


def require_passed_result(path):
    result = read_json(path)
    if not result or result.get('passed') is not True:
        raise RuntimeError('Actual simulation result has not passed; a process exit code is insufficient')
    return result
