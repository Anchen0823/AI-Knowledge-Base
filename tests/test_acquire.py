import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from snakekb import acquire as a, pipeline as p


class AcquisitionRegressionTests(unittest.TestCase):
    def test_fourth_and_fifth_attempt_keep_history_and_use_full_url(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT / 'validation/offline-test-tmp') as temp:
            data = Path(temp)
            bvid = 'BV1WWxT6cEdS'
            with p.use_source(data, bvid):
                root = p.initialize(data) / 'downloads'
                for i in range(3):
                    p.write(root / f'{i:03}' / 'attempt.json', {'status': 'paused', 'reason': 'old'})
                def launch(args, **kwargs):
                    self.assertEqual(args[args.index('--url') + 1], 'https://www.bilibili.com/video/' + bvid + '/')
                    folder = Path(args[args.index('--worker') + 1])
                    p.write(folder / 'attempt.json', {'status': 'paused', 'reason': 'network unavailable'})
                    return Mock()
                with patch.object(a.subprocess, 'Popen', side_effect=launch) as spawn:
                    for count in (4, 5):
                        with self.assertRaisesRegex(p.Paused, 'network unavailable'):
                            a.acquire(data, bvid, retry=True)
                        self.assertEqual(len(list(root.glob('*/attempt.json'))), count)
                    self.assertEqual(spawn.call_count, 2)
                self.assertEqual(p.load(root / '000/attempt.json')['reason'], 'old')

    def test_worker_selects_source_from_its_own_folder(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT / 'validation/offline-test-tmp') as temp:
            folder = Path(temp) / 'pending/BV1WWxT6cEdS/downloads/003'
            def check(actual, url, *args):
                self.assertEqual(p.current().bvid, 'BV1WWxT6cEdS')
                self.assertEqual(url, 'https://www.bilibili.com/video/BV1WWxT6cEdS/')
                self.assertIsNone(p.current().duration)
            with p.use_source(p.DEFAULT_DATA, 'BV1QTHA6DELc'), patch.object(a, '_worker', side_effect=check):
                a.worker(folder, 'BV1WWxT6cEdS')
                self.assertEqual(p.current().bvid, 'BV1QTHA6DELc')

    def test_diagnostics_do_not_include_signed_urls(self):
        reason = a.safe_error(Exception('Unable to connect to proxy https://example.invalid/?token=SECRET'))
        self.assertIn('代理', reason)
        self.assertNotIn('SECRET', reason)
        self.assertIn('完整视频地址', a.safe_error(Exception('not a valid URL')))

    def test_http_error_hides_signed_url(self):
        reason = a.safe_error(Exception('HTTP Error 403 https://cdn.invalid/?secret=TOKEN'))
        self.assertIn('403', reason)
        self.assertNotIn('TOKEN', reason)

    def test_login_error_is_actionable(self):
        self.assertIn('--cookies-file', a.safe_error(Exception('Please log in')))

    def test_decryption_error_does_not_expose_details(self):
        reason = a.safe_error(Exception('DPAPI decrypt SECRET'))
        self.assertIn('解密', reason)
        self.assertNotIn('SECRET', reason)

    def test_unknown_error_does_not_expose_credentials(self):
        self.assertNotIn('SECRET', a.safe_error(Exception('SECRET')))

    def test_timeout_error_is_actionable(self):
        self.assertIn('超时', a.safe_error(Exception('connection timed out')))

    def test_missing_ffmpeg_error_is_actionable(self):
        reason = a.safe_error(Exception('You have requested merging of multiple formats but ffmpeg is not installed. SECRET'))
        self.assertIn('FFmpeg', reason)
        self.assertNotIn('SECRET', reason)

    def test_existing_attempt_requires_explicit_retry(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT / 'validation/offline-test-tmp') as temp:
            data = Path(temp)
            with p.use_source(data, 'BV1WWxT6cEdS'):
                folder = p.initialize(data) / 'downloads/000'
                p.write(folder / 'attempt.json', {'status': 'paused'})
                with patch.object(a.subprocess, 'Popen') as spawn:
                    with self.assertRaisesRegex(p.Paused, 'retry-acquisition'):
                        a.acquire(data, 'BV1WWxT6cEdS')
                    spawn.assert_not_called()

    def test_running_download_is_not_retried(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT / 'validation/offline-test-tmp') as temp:
            data = Path(temp)
            with p.use_source(data, 'BV1WWxT6cEdS'):
                folder = p.initialize(data) / 'downloads/000'
                p.write(folder / 'attempt.json', {'status': 'started'})
                with patch.object(a.subprocess, 'Popen') as spawn:
                    with self.assertRaisesRegex(p.Paused, '可能还在跑'):
                        a.acquire(data, 'BV1WWxT6cEdS', retry=True)
                    spawn.assert_not_called()
