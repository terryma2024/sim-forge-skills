"""Observable workflow contracts; no paid generation or remote mutation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/simforge-asset-pipeline/scripts'


class PipelineTests(unittest.TestCase):
    def cli(self, *args, expected=0):
        script = SCRIPTS / 'pipeline.py'
        self.assertTrue(script.is_file(), 'Reusable pipeline entrypoint is not implemented')
        p = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True)
        self.assertEqual(p.returncode, expected, p.stdout + p.stderr)
        return json.loads(p.stdout)

    def module(self, name):
        path = SCRIPTS / (name + '.py')
        self.assertTrue(path.is_file(), f'{name} helper is not implemented')
        sys.path.insert(0, str(SCRIPTS))
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_reference_checkpoint_approval_is_bound_to_actual_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / 'input.png'; image.write_bytes(b'\x89PNG\r\n\x1a\nreference-one')
            init = self.cli('init', '--run-dir', root/'run', '--prompt', 'A hollow ceramic cup', '--image', image)
            self.assertEqual(init['status'], 'ready')
            waiting = self.cli('run', '--run-dir', root/'run')
            self.assertEqual(waiting['status'], 'awaiting_review')
            self.assertEqual(waiting['checkpoint'], 'reference')
            accepted = self.cli('approve', 'reference', '--run-dir', root/'run')
            self.assertTrue(accepted['approved'])
            self.assertTrue(self.module('common').approval_valid(root/'run', 'reference'))
            (root/'run/assets/reference.png').write_bytes(b'changed-reference')
            self.assertFalse(self.module('common').approval_valid(root/'run', 'reference'))

    def test_end_to_end_requires_reference_instead_of_inventing_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.cli('init', '--run-dir', root, '--prompt', 'A blue cup', '--mode', 'end-to-end')
            result = self.cli('run', '--run-dir', root)
            self.assertEqual(result['status'], 'needs_reference_image')
            self.assertFalse((root/'state/seed3d.json').exists())

    def test_approval_cannot_accept_a_missing_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.cli('init', '--run-dir', root, '--prompt', 'Cup')
            result = self.cli('approve', 'model', '--run-dir', root, expected=1)
            self.assertEqual(result['status'], 'error')

    def test_remote_upload_requires_exact_authorized_destination(self):
        common = self.module('common')
        config = {'isaac': {'host': 'test-host', 'directory': '/data/demo', 'upload_authorized': False}}
        with self.assertRaisesRegex(ValueError, 'authorization'):
            common.require_remote_authorization(config)
        config['isaac']['upload_authorized'] = True
        config['isaac']['authorized_destination'] = {'host': 'test-host', 'directory': '/data/demo'}
        common.require_remote_authorization(config)
        config['isaac']['directory'] = '/data/another-location'
        with self.assertRaisesRegex(ValueError, 'destination'):
            common.require_remote_authorization(config)

    def test_unknown_submit_outcome_is_not_automatically_resubmitted(self):
        seed = self.module('seed3d')
        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            seed.ensure_submission_allowed({'submission_outcome': 'unknown'})
        with self.assertRaisesRegex(RuntimeError, 'existing'):
            seed.ensure_submission_allowed({'task_id': 'existing-task'})

    def test_zip_download_preserves_only_the_single_glb_member(self):
        seed = self.module('seed3d')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); bundle = root/'download.zip'; target = root/'model.glb'
            with zipfile.ZipFile(bundle, 'w') as z:
                z.writestr('../../escape.txt', 'untrusted')
                z.writestr('pbr/mesh.glb', b'glTFmodel-data')
            seed.extract_glb(bundle, target)
            self.assertEqual(target.read_bytes(), b'glTFmodel-data')
            self.assertFalse((root.parent/'escape.txt').exists())

    def test_zero_exit_code_does_not_prove_isaac_test_passed(self):
        common = self.module('common')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); result = root/'simulation_result.json'
            result.write_text(json.dumps({'passed': False, 'metrics': {}}))
            with self.assertRaisesRegex(RuntimeError, 'passed'):
                common.require_passed_result(result)

    def test_missing_key_does_not_record_an_unknown_paid_submission(self):
        seed=self.module('seed3d'); common=self.module('common')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'assets').mkdir(); (root/'assets/reference.png').write_bytes(b'reference')
            c=common.merge(common.DEFAULTS,{'seed3d':{'key_env':'SIMFORGE_ABSENT_TEST_KEY','env_file':str(root/'missing.env')}})
            with mock.patch.dict(os.environ,{},clear=True), self.assertRaises(ValueError):
                seed.submit(root,c)
            self.assertFalse((root/'state/seed3d.json').exists(),'No HTTP request occurred, so no submission intent should be recorded')

    def test_explicit_api_rejection_is_distinct_from_unknown_outcome(self):
        seed=self.module('seed3d'); common=self.module('common')
        self.assertTrue(hasattr(seed,'ArkHTTPError'),'API rejection needs a typed HTTP outcome')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'assets').mkdir(); (root/'assets/reference.png').write_bytes(b'reference')
            c=common.merge(common.DEFAULTS,{'seed3d':{'key_env':'SIMFORGE_TEST_KEY'}})
            with mock.patch.dict(os.environ,{'SIMFORGE_TEST_KEY':'non-secret-test-fixture'}), mock.patch.object(seed,'request',side_effect=seed.ArkHTTPError(401,'rejected')):
                with self.assertRaises(RuntimeError): seed.submit(root,c)
            self.assertEqual(json.loads((root/'state/seed3d.json').read_text())['submission_outcome'],'rejected')

    def test_existing_glb_does_not_require_a_new_reference_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); glb=root/'input.glb'
            chunk=b'{"asset":{"version":"2.0"}}  '
            while len(chunk)%4: chunk+=b' '
            glb.write_bytes(struct.pack('<4sII',b'glTF',2,20+len(chunk))+struct.pack('<II',len(chunk),0x4E4F534A)+chunk)
            self.cli('init','--run-dir',root/'run','--prompt','Use this existing cup','--mode','end-to-end')
            self.cli('import-model','--run-dir',root/'run','--glb',glb)
            result=self.cli('run','--run-dir',root/'run')
            self.assertEqual(result['status'],'needs_environment')

    def test_moving_pinned_dependency_path_keeps_completed_receipts_valid(self):
        pipeline=self.module('pipeline'); common=self.module('common')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'assets').mkdir(); (root/'assets/source.glb').write_bytes(b'source')
            c=common.merge(common.DEFAULTS,{'simforge':{'source_dir':'/original/cache/source'}})
            common.write_json(root/'config.json',c)
            pipeline.receipt(root,c,'model',[root/'assets/source.glb'])
            c['simforge']['source_dir']='/moved/cache/source'; common.write_json(root/'config.json',c)
            try: result=pipeline.completed(root,'model')
            except RuntimeError as error: self.fail(str(error))
            self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
