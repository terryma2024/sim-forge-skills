"""Resumable reference → 3D → Blender → USD → actual Isaac verification CLI."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from common import (DEFAULTS, CHECKPOINTS, approval_valid, approve, blender_executable,
    checkpoint_files, config_at, merge, read_json, require_passed_result,
    require_remote_authorization, sha256, validate_config, write_json)

SCRIPTS = Path(__file__).resolve().parent


def emit(value):
    print(json.dumps(value, ensure_ascii=False))


@contextlib.contextmanager
def lock(run):
    path = run/'state/runner.lock'
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError('A runner lock exists. Inspect its PID and any recorded remote job before removing a stale lock.')
    with os.fdopen(fd, 'w') as stream:
        stream.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def process(command, log, expected=None):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as stream:
        p = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
    if p.returncode:
        raise RuntimeError(f'Worker exited {p.returncode}; inspect {log}')
    if expected is not None and not expected.is_file():
        raise RuntimeError(f'Worker produced no required result {expected}; inspect {log}')


def worker(run, c, action):
    expected = {'import': 'source_report.json', 'retopo': 'retopo_report.json',
                'bake': 'bake_report.json', 'export': 'asset_validation.json'}[action]
    process([blender_executable(c), '--background', '--factory-startup', '--python-exit-code', '1',
             '--python', str(SCRIPTS/'blender_asset.py'), '--', action, str(run)],
            run/'logs'/f'blender-{action}.log', run/'assets'/expected)


def processing_config(config):
    # A pinned dependency can move between machines without changing assets.
    # Preserve numeric processing settings, not machine-specific executable paths.
    return {'asset':config['asset'],
            'blender':{k:v for k,v in config['blender'].items() if k!='executable'},
            'simforge':{k:v for k,v in config['simforge'].items() if k!='source_dir'}}


def completed(run, stage):
    receipt = read_json(run/'state/receipts.json', {}).get(stage)
    if not receipt:
        return False
    config = config_at(run)
    effective = processing_config(config)
    if processing_config(receipt['config']) != effective:
        raise RuntimeError('Asset processing configuration changed after a checkpoint; create a new run directory')
    for relative, digest in receipt['files'].items():
        path = run/relative
        if not path.is_file() or sha256(path) != digest:
            raise RuntimeError(f'Completed {stage} artifact changed: {relative}; create a new run')
    return True


def receipt(run, c, stage, paths):
    receipts = read_json(run/'state/receipts.json', {})
    receipts[stage] = {'config': processing_config(c),
                       'files': {str(p.relative_to(run)): sha256(p) for p in paths}}
    write_json(run/'state/receipts.json', receipts)


def review(run, c, checkpoint):
    paths = checkpoint_files(run, checkpoint)
    if c['mode'] == 'end-to-end' or approval_valid(run, checkpoint):
        return None
    return {'status': 'awaiting_review', 'checkpoint': checkpoint,
            'artifacts': [str(p) for p in paths],
            'next': f'approve {checkpoint} --run-dir {shlex.quote(str(run))}; then run --run-dir ...'}


def command_output(command):
    return subprocess.check_output(command, text=True, stderr=subprocess.PIPE).strip()


def remote_simulation(run, c):
    """Launch once, then observe the saved remote PID/result on later resumes."""
    require_remote_authorization(c)
    i = c['isaac']; host = i['host']
    if not i.get('python') or not i.get('eula_accepted'):
        raise ValueError('Set isaac.python and record the user\'s existing Isaac EULA acceptance')
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host]
    remote_path = run/'state/remote.json'
    state = read_json(remote_path, {})
    destination = {'host': host, 'directory': i['directory']}
    if state and state['destination'] != destination:
        raise ValueError('Recorded remote job belongs to another destination')
    if not state:
        attempt = i['directory'].rstrip('/') + '/run-' + uuid.uuid4().hex[:12]
        state = {'destination': destination, 'attempt': attempt, 'status': 'uploading'}
        write_json(remote_path, state)
        command_output(ssh + ['mkdir -p '+shlex.quote(attempt+'/assets')+' '+shlex.quote(attempt+'/scripts')])
        a = run/'assets'
        files = [a/'pipeline_report.json', a/'runtime_config.json']
        report = read_json(a/'pipeline_report.json')
        for filename in report['usd_assets'].values():
            files.extend([a/filename, a/(Path(filename).stem+'_textures')])
        subprocess.run(['scp','-r',*map(str,files),host+':'+attempt+'/assets/'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        subprocess.run(['scp',str(SCRIPTS/'isaac_verify.py'),host+':'+attempt+'/scripts/'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        state['status'] = 'launch_outcome_unknown'; write_json(remote_path, state)
        argv = ['env', 'OMNI_KIT_ACCEPT_EULA=YES', i['python'], attempt+'/scripts/isaac_verify.py',
                attempt+'/assets', '--/app/window/enabled=false',
                '--/exts/isaacsim.core.throttling/enable_async=false',
                '--/exts/omni.kit.registry.nucleus/httpReachabilityTimeout=60']
        shell = 'nohup '+shlex.join(argv)+' > '+shlex.quote(attempt+'/simulation.log')+' 2>&1 < /dev/null & echo $! > '+shlex.quote(attempt+'/process.pid')
        command_output(ssh + [shell])
        state['status'] = 'running'; write_json(remote_path, state)
    if state['status'] == 'uploading':
        raise RuntimeError('Upload was interrupted. Inspect recorded attempt; archive this run and start a fresh local copy to retry')
    attempt = state['attempt']
    deadline = time.monotonic() + 45
    while True:
        check = ('if test -f '+shlex.quote(attempt+'/assets/simulation_result.json')+
                 '; then echo RESULT; elif test -f '+shlex.quote(attempt+'/process.pid')+
                 ' && kill -0 "$(cat '+shlex.quote(attempt+'/process.pid')+')" 2>/dev/null; then echo RUNNING; else echo TERMINAL; fi')
        status = command_output(ssh + [check]).splitlines()[-1]
        if status == 'RESULT':
            # Presence of JSON alone is not completion: worker writes after final verdict.
            subprocess.run(['scp',host+':'+attempt+'/assets/simulation_result.json',str(run/'assets/')],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            result = require_passed_result(run/'assets/simulation_result.json')
            # Wait for frame flush/worker exit before fetching the sequence.
            if not result.get('frames_saved'):
                raise RuntimeError('Actual simulation has no RGB frames')
            for name in ('simulation_frames','cup_comparison_scene.usda'):
                subprocess.run(['scp','-r',host+':'+attempt+'/assets/'+name,str(run/'assets/')],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            subprocess.run(['scp',host+':'+attempt+'/simulation.log',str(run/'logs/isaac.log')],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            state['status']='passed'; write_json(remote_path,state)
            return result
        if status == 'TERMINAL':
            raise RuntimeError(f'Remote job ended without a passing result. Inspect {host}:{attempt}/simulation.log; no automatic relaunch')
        if time.monotonic() >= deadline:
            return {'status':'simulation_running','host':host,'attempt':attempt}
        time.sleep(5)


def simulation(run, c):
    a = run/'assets'
    runtime={'asset':c['asset'], 'isaac':{k:c['isaac'][k] for k in
        ('test','physics_hz','duration_s','ball_angular_damping','contact_offset_m','stability_tolerance_m')}}
    previous=read_json(a/'runtime_config.json')
    if previous and previous!=runtime and ((run/'state/remote.json').exists() or (a/'simulation_result.json').exists()):
        raise ValueError('Simulation configuration changed after launch; initialize a new run')
    if (a/'simulation_result.json').exists():
        result=require_passed_result(a/'simulation_result.json')
        if len(list((a/'simulation_frames').glob('frame_*.png')))==result['frames_saved']:
            return result
        if not c['isaac']['host']:
            raise RuntimeError('Local actual RGB sequence is incomplete; preserve this failed attempt')
    # Upload only runtime physics settings, never the API credential source or task response.
    write_json(a/'runtime_config.json',runtime)
    if c['isaac']['host']:
        return remote_simulation(run,c)
    if sys.platform == 'darwin':
        return {'status':'needs_isaac_machine','asset_stage_complete':True,
                'detail':'Mac can prepare assets; configure an authorized Linux/Windows NVIDIA Isaac runtime'}
    if not c['isaac']['python'] or not c['isaac']['eula_accepted']:
        return {'status':'needs_isaac_environment','asset_stage_complete':True}
    env=os.environ.copy(); env['OMNI_KIT_ACCEPT_EULA']='YES'
    with (run/'logs/isaac.log').open('w') as log:
        subprocess.run([c['isaac']['python'],str(SCRIPTS/'isaac_verify.py'),str(a),
            '--/app/window/enabled=false','--/exts/isaacsim.core.throttling/enable_async=false'],
            stdout=log,stderr=subprocess.STDOUT,env=env,check=True)
    return require_passed_result(a/'simulation_result.json')


def encode(run):
    a=run/'assets'; frames=sorted((a/'simulation_frames').glob('frame_*.png'))
    if not frames:
        raise RuntimeError('No actual RGB frames to encode')
    result=require_passed_result(a/'simulation_result.json')
    if len(frames) != result['frames_saved']:
        raise RuntimeError('Retrieved RGB sequence is incomplete')
    fps=result['recording_fps']
    process(['ffmpeg','-hide_banner','-y','-framerate',str(fps),'-i',str(a/'simulation_frames/frame_%04d.png'),
        '-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(a/'simulation.mp4')],
        run/'logs/video.log', a/'simulation.mp4')
    shutil.copy2(frames[0],a/'simulation_start.png'); shutil.copy2(frames[-1],a/'simulation_final.png')
    (a/'SIMULATION.md').write_text('Actual Isaac RGB recording. Cup comparison: left=convex decomposition; right=convex hull.\n'
        +'Runtime settings and trajectories are in simulation_result.json. No real-material calibration is implied.\n')


def run_pipeline(run, until):
    c=config_at(run); a=run/'assets'
    with lock(run):
        if not (a/'reference.png').is_file() and not (a/'source.glb').is_file():
            return {'status':'needs_reference_image','prompt':c['prompt'],
                    'next':'Generate the image with the agent image tool, then provide-reference --image ...'}
        waiting=review(run,c,'reference') if (a/'reference.png').is_file() else None
        if waiting:
            return waiting
        source=c['simforge']['source_dir']
        if not source or not (Path(source).expanduser()/'simforge/core/contracts.py').is_file():
            return {'status':'needs_environment','next':'bootstrap --run-dir ...; then doctor --run-dir ...'}
        blender_executable(c)
        if not (a/'source.glb').exists():
            from seed3d import generate
            result=generate(run,c)
            if result.get('status')=='generation_pending':
                return result
        if not completed(run,'model'):
            worker(run,c,'import')
            paths=[a/'source.glb',a/'source_provenance.json',a/'source.blend',a/'source_report.json',a/'source_preview.png']
            if (a/'reference.png').is_file(): paths.append(a/'reference.png')
            receipt(run,c,'model',paths)
        waiting=review(run,c,'model')
        if waiting:
            return waiting
        if c['asset']['require_cavity'] and read_json(a/'source_report.json').get('cavity_verified') is False:
            return {'status':'geometry_needs_review','stage':'model','preview':str(a/'source_preview.png'),
                    'detail':'The actual generated model has no verified cavity; choose a new model/run'}
        for action,outputs in [('retopo',['retopo.blend','retopo_report.json']),
                               ('bake',['baked.blend','bake_report.json']),
                               ('export',['asset_pipeline.blend','pipeline_report.json','asset_validation.json','high_low_comparison.png'])]:
            if not completed(run,action):
                worker(run,c,action)
                paths=[a/name for name in outputs]
                if action=='bake': paths.extend(sorted((a/'baked_textures').glob('*.png')))
                if action=='export':
                    for name in read_json(a/'pipeline_report.json')['usd_assets'].values():
                        paths.append(a/name); paths.extend(sorted((a/(Path(name).stem+'_textures')).glob('*.png')))
                receipt(run,c,action,paths)
            if action=='retopo' and c['asset']['require_cavity'] and read_json(a/'retopo_report.json').get('cavity_verified') is False:
                return {'status':'geometry_needs_review','stage':'retopo','preview':str(a/'retopo_preview.png'),
                        'detail':'Retopology did not preserve a usable cavity; review the saved low-poly checkpoint'}
        waiting=review(run,c,'blender')
        if waiting:
            return waiting
        if until=='assets':
            return {'status':'assets_complete','simulation_verified':False,'assets':str(a)}
        result=simulation(run,c)
        if result.get('passed') is not True:
            return result
        if not (a/'simulation.mp4').is_file():
            encode(run)
        waiting=review(run,c,'simulation')
        if waiting:
            return waiting
        write_json(a/'completion.json',{'passed':True,'scope':c['isaac']['test'],
            'mode':c['mode'],'asset_validation':'asset_validation.json','simulation_result':'simulation_result.json',
            'video':'simulation.mp4','real_materials_calibrated':False})
        return {'status':'complete','assets':str(a),'video':str(a/'simulation.mp4')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    init=commands.add_parser('init'); init.add_argument('--run-dir',required=True); init.add_argument('--prompt',required=True)
    init.add_argument('--config'); init.add_argument('--mode',choices=['checkpoint','end-to-end']); init.add_argument('--image')
    for name in ('run','status','doctor','bootstrap'):
        p=commands.add_parser(name); p.add_argument('--run-dir',required=True)
        if name=='run': p.add_argument('--until',choices=['assets','simulation'],default='simulation')
    p=commands.add_parser('approve'); p.add_argument('checkpoint',choices=CHECKPOINTS); p.add_argument('--run-dir',required=True)
    p=commands.add_parser('provide-reference'); p.add_argument('--image',required=True); p.add_argument('--run-dir',required=True)
    p=commands.add_parser('import-model'); p.add_argument('--glb',required=True); p.add_argument('--provenance'); p.add_argument('--run-dir',required=True)
    p=commands.add_parser('adopt-task'); p.add_argument('--task-id',required=True); p.add_argument('--run-dir',required=True)
    args=parser.parse_args(); run=Path(args.run_dir).expanduser().resolve()
    if args.command=='init':
        if (run/'config.json').exists(): raise ValueError('Run already initialized; use status/run')
        c=merge(DEFAULTS,read_json(args.config,{}) if args.config else {})
        c['prompt']=args.prompt
        if args.mode: c['mode']=args.mode
        validate_config(c)
        for name in ('assets','state','logs'): (run/name).mkdir(parents=True,exist_ok=True)
        write_json(run/'config.json',c)
        if args.image: shutil.copy2(Path(args.image).expanduser(),run/'assets/reference.png')
        return {'status':'ready','run_dir':str(run),'mode':c['mode']}
    c=config_at(run)
    if args.command=='run': return run_pipeline(run,args.until)
    if args.command=='approve':
        approve(run,args.checkpoint); return {'approved':True,'checkpoint':args.checkpoint}
    if args.command=='provide-reference':
        if (run/'assets/source.glb').exists() or (run/'state/seed3d.json').exists():
            raise ValueError('A 3D task already uses this reference; initialize a new run for changes')
        shutil.copy2(args.image,run/'assets/reference.png'); return {'status':'reference_ready'}
    if args.command=='import-model':
        from seed3d import validate_glb
        source=Path(args.glb).expanduser(); validate_glb(source)
        if (run/'assets/source.glb').exists(): raise ValueError('Source already exists; preserve it')
        provenance=read_json(args.provenance,{}) if args.provenance else {'source':'user-supplied'}
        claimed=provenance.get('glb_sha256')
        if claimed and claimed != sha256(source): raise ValueError('Supplied source hash differs from provenance')
        shutil.copy2(source,run/'assets/source.glb')
        provenance.update(glb_sha256=sha256(source),glb_bytes=source.stat().st_size)
        write_json(run/'assets/source_provenance.json',provenance)
        return {'status':'model_imported','new_generation_submitted':False}
    if args.command=='adopt-task':
        from seed3d import poll
        state=read_json(run/'state/seed3d.json',{})
        if state.get('task_id') and state['task_id']!=args.task_id: raise ValueError('Another task is already recorded')
        state.update(task_id=args.task_id,submission_outcome='adopted',model=c['seed3d']['model'])
        write_json(run/'state/seed3d.json',state); response=poll(run,c)
        return {'status':'task_adopted','task_id':args.task_id,'task_status':response['task_status']}
    if args.command in ('doctor','bootstrap'):
        from environment import doctor,bootstrap
        return doctor(run,c) if args.command=='doctor' else bootstrap(run,c)
    return {'config':c,'receipts':read_json(run/'state/receipts.json',{}),
            'approvals':read_json(run/'state/approvals.json',{}),
            'remote':read_json(run/'state/remote.json',{})}


if __name__=='__main__':
    try: emit(main())
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as error:
        # Workers and Ark state may contain private details; errors give artifact locations.
        emit({'status':'error','message':str(error)}); sys.exit(1)
