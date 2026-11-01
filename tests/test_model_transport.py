import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from snakekb import pipeline as p, concepts


class ModelTransportTests(unittest.TestCase):
    def test_both_model_paths_use_https_proxy_and_wait_without_deadline(self):
        for kind in ('analysis', 'concepts'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(
                    dir=p.ROOT / 'validation/offline-test-tmp') as temp:
                root = Path(temp)
                cli = root / 'node_modules/@openai/codex/bin/codex.js'
                cli.parent.mkdir(parents=True)
                cli.touch()
                p.write(root / 'state.json', {'status': 'prepared'})
                child = Mock(returncode=1)
                with patch.object(p, 'tool', return_value=str(root / 'codex.cmd')), \
                        patch.object(p.subprocess, 'Popen', return_value=child) as spawn, \
                        patch.object(p, 'input_gate', return_value=[]), \
                        patch.object(p, 'batches', return_value=[{'frames': []}]), \
                        patch.object(p, 'make_prompt', return_value='prompt'):
                    # A failed child exits promptly, without starting another call.
                    with self.assertRaises(p.Paused):
                        if kind == 'analysis':
                            p.analyze(root, 'http://127.0.0.1:65532')
                        else:
                            concepts.generate(root, root, 'entry', 'title',
                                              '- **原作者陈述**：测试结论\n',
                                              'http://127.0.0.1:65532')
                self.assertEqual(spawn.call_count, 1)
                command = spawn.call_args.args[0]
                self.assertIn('model_provider="openai-https"', command)
                self.assertTrue(any('supports_websockets=false' in value for value in command))
                self.assertTrue(any('requires_openai_auth=true' in value for value in command))
                self.assertEqual(spawn.call_args.kwargs['env']['HTTPS_PROXY'],
                                 'http://127.0.0.1:65532')
                child.communicate.assert_called_once()
                self.assertNotIn('timeout', child.communicate.call_args.kwargs)
                record = p.load(next(root.glob('**/call.json')))
                self.assertIsNone(record['timeout_seconds'])
                self.assertEqual(record['transport'], 'https')
