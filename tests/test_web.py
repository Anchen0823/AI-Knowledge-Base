import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

from snakekb import pipeline as p
from snakekb.web import make_server


class WebTests(unittest.TestCase):
    def setUp(self):
        base = p.ROOT / 'validation/offline-test-tmp'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.data = Path(self.temp.name)
        self.server = make_server(self.data, port=0, token='test-token')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, obj=None, auth=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Authorization': 'Bearer test-token'} if auth else {}
        body = json.dumps(obj) if obj is not None else None
        conn.request('POST' if obj is not None else 'GET', path, body, headers)
        response = conn.getresponse()
        result = response.status, response.read().decode()
        conn.close()
        return result

    def test_private_api_requires_access_token(self):
        self.assertEqual(self.request('/api/state', auth=False)[0], 401)
        self.assertEqual(self.request('/api/state')[0], 200)

    def test_preparing_job_without_frames_keeps_state_available(self):
        pending = p.initialize(self.data)
        p.write(pending / '0123456789abcdef0123/state.json', {'status': 'preparing'})
        status, body = self.request('/api/state')
        self.assertEqual(status, 200)
        job = json.loads(body)['jobs'][0]
        self.assertEqual(job['status'], 'preparing')
        self.assertEqual(job['batches'], 0)

    def test_query_never_returns_pending_material(self):
        (p.initialize(self.data) / 'private.txt').write_text('PENDING_SECRET')
        status, body = self.request('/api/query')
        self.assertEqual(status, 200)
        self.assertNotIn('PENDING_SECRET', body)
        self.assertIn('没有通过门禁', body)

    def test_submit_is_deduplicated_and_rejects_private_url(self):
        self.assertEqual(self.request('/api/submit', {'url': 'https://b23.tv/Zemg4oc'})[0], 200)
        self.assertEqual(self.request('/api/submit', {'url': 'https://b23.tv/Zemg4oc'})[0], 200)
        self.assertEqual(self.request('/api/submit', {'url': 'http://127.0.0.1/private'})[0], 400)
        self.assertFalse((self.data / 'formal').exists())

    def test_no_model_action_without_consent(self):
        self.assertEqual(self.request('/api/analyze', {'job': '../anything'})[0], 400)

    def test_new_link_is_remembered_before_downloading(self):
        status, body = self.request('/api/submit', {'url': 'https://www.bilibili.com/video/BV1WWNx6tcd5/'})
        self.assertEqual(status, 200)
        message = json.loads(body)['message']
        self.assertIn('BV1WWNx6tcd5', message)
        self.assertIn('勾选', message)
        self.assertFalse((self.data / 'pending').exists(), '没同意之前不应该动本地素材')

    def test_import_outside_default_data_is_refused(self):
        status, body = self.request('/api/submit', {'url': 'BV1WWNx6tcd5', 'allow_cloud': True})
        self.assertEqual(status, 400)
        self.assertIn('data', json.loads(body)['error'])

    def test_failed_import_retries_and_double_click_does_not_spawn_twice(self):
        folder = p.initialize(self.data)
        record = p.load(folder / 'acquisition.json')
        record.update(status='paused_ingest', reasons=['download failed'])
        p.write(folder / 'acquisition.json', record)
        process = Mock()
        process.poll.return_value = None
        with patch.object(p, 'DEFAULT_DATA', self.data), patch('snakekb.web.subprocess.Popen', return_value=process) as spawn:
            payload = {'url': p.BVID, 'allow_cloud': True}
            self.assertEqual(self.request('/api/submit', payload)[0], 202)
            self.assertIn('--retry', spawn.call_args.args[0])
            self.assertEqual(self.request('/api/submit', payload)[0], 200)
            self.assertEqual(spawn.call_count, 1)
        self.assertEqual(p.load(folder / 'acquisition.json')['reasons'], ['download failed'])

    def test_failed_import_still_respects_unchecked_consent(self):
        folder = p.initialize(self.data)
        record = p.load(folder / 'acquisition.json')
        record['status'] = 'paused_ingest'
        p.write(folder / 'acquisition.json', record)
        with patch('snakekb.web.subprocess.Popen') as spawn:
            self.assertEqual(self.request('/api/submit', {'url': p.BVID, 'allow_cloud': False})[0], 200)
            spawn.assert_not_called()

    def test_formal_frame_allowlist_and_path_isolation(self):
        name = p.BVID + '-fixture'
        entry = self.data / 'formal' / name
        p.write(entry / 'gate.json', {'passed': True})
        (entry / 'notes.md').write_text('FORMAL_ONLY')
        p.write(entry / 'frames.json', [{'file': 'frames/0001.jpg'}])
        (entry / 'frames').mkdir()
        (entry / 'frames/0001.jpg').write_bytes(b'jpeg-fixture')
        p.write(self.data / 'formal/index.json', {p.BVID: name})
        self.assertEqual(self.request('/api/frame/0001.jpg'), (200, 'jpeg-fixture'))
        self.assertEqual(self.request('/api/frame/../../private.txt')[0], 404)
        self.assertEqual(self.request('/api/frame/0001.jpg', auth=False)[0], 401)

    def test_multiple_sources_are_isolated_and_pending_stays_private(self):
        names = {}
        for bvid in (p.BVID, 'BV1kind6mER6'):
            name = bvid + '-fixture'
            names[bvid] = name
            entry = self.data / 'formal' / name
            p.write(entry / 'gate.json', {'passed': True})
            p.write(entry / 'source.json', {'bvid': bvid, 'title': bvid, 'canonical': 'https://www.bilibili.com/video/' + bvid})
            (entry / 'notes.md').write_text('NOTES:' + bvid)
            p.write(entry / 'frames.json', [{'file': 'frames/0001.jpg'}])
            (entry / 'frames').mkdir()
            (entry / 'frames/0001.jpg').write_bytes(bvid.encode())
        p.write(self.data / 'formal/index.json', names)
        status, body = self.request('/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)['library']), 2)
        second = names['BV1kind6mER6']
        self.assertEqual(self.request('/api/frame/0001.jpg?entry=' + second), (200, 'BV1kind6mER6'))
        self.assertEqual(self.request('/api/frame/0001.jpg?entry=../../pending')[0], 404)
        self.assertIn('NOTES:BV1kind6mER6', self.request('/api/query?entry=' + second)[1])
        p.write(self.data / 'formal' / second / 'gate.json', {'passed': False})
        self.assertEqual(len(json.loads(self.request('/api/state')[1])['library']), 1)
        self.assertEqual(self.request('/api/query?entry=' + second)[0], 404)
        self.assertEqual(self.request('/api/frame/0001.jpg?entry=' + second)[0], 404)


class ErrorResponseTests(unittest.TestCase):
    """报错要能说清发生了什么，并且给出下一步。"""

    def setUp(self):
        base = p.ROOT / 'validation/offline-test-tmp'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.data = Path(self.temp.name)
        self.server = make_server(self.data, port=0, token='test-token')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, obj=None, auth=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Authorization': 'Bearer test-token'} if auth else {}
        body = json.dumps(obj) if obj is not None else None
        conn.request('POST' if obj is not None else 'GET', path, body, headers)
        response = conn.getresponse()
        result = response.status, json.loads(response.read().decode()).get('error', '')
        conn.close()
        return result

    def test_missing_token_error_says_how_to_get_one(self):
        status, error = self.request('/api/state', auth=False)
        self.assertEqual(status, 401)
        self.assertIn('#key=', error)

    def test_bad_graph_numbers_return_actionable_message(self):
        status, error = self.request('/api/graph?limit=abc')
        self.assertEqual(status, 400)
        self.assertIn('刷新页面', error)

    def test_paused_rejection_reaches_client_as_400(self):
        status, error = self.request('/api/submit', {'url': 'not a link'})
        self.assertEqual(status, 400)
        self.assertIn('链接', error)

    def test_unknown_path_is_404(self):
        status, error = self.request('/api/nope')
        self.assertEqual(status, 404)

    def test_oversized_body_is_rejected(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        conn.request('POST', '/api/submit', 'x' * 9000,
                     {'Authorization': 'Bearer test-token', 'Content-Type': 'application/json'})
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()


class PortFallbackTests(unittest.TestCase):
    def test_busy_port_falls_back_to_next_one(self):
        from snakekb.web import bind
        base = p.ROOT / 'validation/offline-test-tmp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            first, first_port = bind(Path(temp), '127.0.0.1', 18100, token='a')
            try:
                # 同一个端口再开一次必须换位，而不是直接报错退出。
                second, second_port = bind(Path(temp), '127.0.0.1', first_port, token='b')
                try:
                    self.assertEqual(second_port, first_port + 1)
                finally:
                    second.server_close()
            finally:
                first.server_close()


class ModelEnvironmentTests(unittest.TestCase):
    def test_retry_preserves_history_and_completed_batches(self):
        base = p.ROOT / 'validation/offline-test-tmp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            job = Path(temp)
            p.write(job / 'state.json', {'status': 'paused', 'reason': 'old failure'})
            p.write(job / 'calls/000/call.json', {'status': 'complete'})
            p.write(job / 'calls/001/call.json', {'status': 'failed'})
            p.reopen_analysis(job)
            self.assertTrue((job / 'calls/000/call.json').exists())
            self.assertFalse((job / 'calls/001').exists())
            self.assertEqual(len(list((job / 'call-history').glob('*/call.json'))), 1)
            self.assertNotIn('reason', p.load(job / 'state.json'))

    def test_proxy_only_changes_child_environment(self):
        import os
        before = dict(os.environ)
        env = p.cli_environment('http://127.0.0.1:65532')
        self.assertEqual(env['HTTPS_PROXY'], 'http://127.0.0.1:65532')
        self.assertEqual(dict(os.environ), before)
        with self.assertRaises(p.Paused):
            p.cli_environment('http://user:secret@127.0.0.1:65532')
