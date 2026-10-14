"""Single-submission Ark client. Private task state stays in the run directory."""
import base64
import json
from pathlib import Path
import shutil
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from common import credential, read_json, sha256, write_json


class ArkHTTPError(RuntimeError):
    def __init__(self,status,message):
        self.status=status
        super().__init__(f'Ark HTTP {status}: {message}')


def ensure_submission_allowed(state):
    if state.get('submission_outcome') == 'unknown':
        raise RuntimeError('Submission outcome is unknown. Find the existing task in Ark, then use adopt-task; do not submit again.')
    if state.get('task_id'):
        raise RuntimeError('An existing task is recorded; poll that task instead of resubmitting')


def extract_glb(bundle, target):
    with zipfile.ZipFile(bundle) as z:
        if z.testzip() is not None:
            raise ValueError('Generated ZIP failed its CRC check')
        members = [n for n in z.namelist() if n.lower().endswith('.glb')]
        if len(members) != 1:
            raise ValueError('Expected exactly one generated GLB; inspect the package')
        # Copy only this member, without interpreting its archive path.
        with z.open(members[0]) as source, Path(target).open('xb') as destination:
            shutil.copyfileobj(source, destination)
        return members[0]


def validate_glb(path):
    path = Path(path)
    with path.open('rb') as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise ValueError('Truncated GLB header')
        magic, version, length = struct.unpack('<4sII', header)
        if magic != b'glTF' or version != 2 or length != path.stat().st_size:
            raise ValueError('Invalid or truncated GLB; preserve evidence and re-download')
        consumed = 12
        while consumed < length:
            chunk_header = stream.read(8)
            if len(chunk_header) != 8:
                raise ValueError('Truncated GLB chunk')
            chunk_length, kind = struct.unpack('<II', chunk_header)
            if consumed + 8 + chunk_length > length:
                raise ValueError('GLB chunk exceeds declared length')
            stream.seek(chunk_length, 1)
            consumed += chunk_length + 8
        if consumed != length:
            raise ValueError('Invalid GLB chunk lengths')


def request(c, method, path, body=None):
    key = credential(c)
    url = c['seed3d']['base_url'].rstrip('/') + '/contents/generations/tasks' + path
    if urllib.parse.urlparse(url).scheme != 'https':
        raise ValueError('Ark credentials require HTTPS')
    req = urllib.request.Request(url, method=method, headers={'Authorization': 'Bearer '+key,
        'Content-Type': 'application/json'}, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        message = error.read().decode(errors='replace').replace(key, '[REDACTED]')
        raise ArkHTTPError(error.code,message) from None


def submit(run, c):
    state_path = Path(run)/'state/seed3d.json'
    state = read_json(state_path, {})
    ensure_submission_allowed(state)
    credential(c)  # Missing local credentials mean no HTTP submission was attempted.
    image = Path(run)/'assets/reference.png'
    body = {'model': c['seed3d']['model'], 'content': [
        {'type': 'text', 'text': '--subdivisionlevel '+c['seed3d']['subdivision']+' --fileformat glb'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode()}}]}
    # Persist intent BEFORE the paid POST. Timeout must never trigger a duplicate.
    state = {'submission_outcome': 'unknown', 'model': body['model'],
             'image_sha256': sha256(image), 'requested_subdivision': c['seed3d']['subdivision']}
    write_json(state_path, state)
    try:
        response = request(c, 'POST', '', body)
    except ArkHTTPError as error:
        # A 4xx authorization/validation rejection has a known non-accepted outcome.
        # 5xx can follow server-side acceptance, so keep it unknown.
        if 400 <= error.status < 500:
            state.update(submission_outcome='rejected',http_status=error.status)
            write_json(state_path,state)
        raise
    if not response.get('id'):
        raise RuntimeError('Ark returned no task ID; submission remains unknown')
    state.update(task_id=response['id'], submission_outcome='accepted')
    write_json(state_path, state)
    return state


def poll(run, c):
    path = Path(run)/'state/seed3d.json'; state = read_json(path, {})
    if not state.get('task_id'):
        raise RuntimeError('No task ID; use submit or adopt-task')
    response = request(c, 'GET', '/'+urllib.parse.quote(state['task_id'], safe=''))
    state.update(task_status=response.get('status'), task_response=response)
    write_json(path, state)
    return state


def download(run, c, state):
    out = Path(run)/'assets'; target = out/'source.glb'
    if not target.exists():
        url = state.get('task_response', {}).get('content', {}).get('file_url')
        if not url or urllib.parse.urlparse(url).scheme != 'https':
            raise RuntimeError('Completed Ark task has no HTTPS file_url')
        # The URL can be signed; do not print it or send the Ark key to the CDN.
        temp = out/'source.download.partial'
        if temp.exists():
            raise RuntimeError('A partial download exists; preserve it under another name before retrying')
        with urllib.request.urlopen(url, timeout=120) as source, temp.open('xb') as destination:
            shutil.copyfileobj(source, destination, length=1024*1024)
        with temp.open('rb') as stream:
            magic = stream.read(4)
        if magic == b'glTF':
            validate_glb(temp); temp.replace(target)
        elif zipfile.is_zipfile(temp):
            extracted = out/'source.extracted.partial'
            member = extract_glb(temp, extracted)
            validate_glb(extracted); extracted.replace(target)
            temp.replace(out/'source_original_package.zip')
            state['archive_member'] = member
        else:
            raise ValueError('Seed3D response is neither a GLB nor a ZIP containing one GLB')
    validate_glb(target)
    provenance = {k: state[k] for k in ('task_id','model','image_sha256','requested_subdivision') if k in state}
    provenance.update(source='Seed3D', glb_sha256=sha256(target), glb_bytes=target.stat().st_size)
    write_json(out/'source_provenance.json', provenance)
    return provenance


def generate(run, c):
    state = read_json(Path(run)/'state/seed3d.json', {})
    if not state.get('task_id'):
        state = submit(run, c)
    deadline = time.monotonic() + c['seed3d']['wait_seconds']
    while True:
        state = poll(run, c)
        status = state.get('task_status')
        if status == 'succeeded':
            return download(run, c, state)
        if status in ('failed', 'cancelled', 'expired'):
            raise RuntimeError(f'Existing Seed3D task ended with status {status}; inspect private task state')
        if time.monotonic() >= deadline:
            return {'status': 'generation_pending', 'task_id': state['task_id'], 'task_status': status}
        time.sleep(min(c['seed3d']['poll_seconds'], max(0, deadline-time.monotonic())))
