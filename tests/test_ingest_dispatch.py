"""新来源进来时，按素材形态自动选路：字幕 -> 转写 -> 画面文字。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from snakekb import ingest
from snakekb import pipeline as p


class DispatchTests(unittest.TestCase):
    BVID = 'BV1WWNx6tcd5'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=p.ROOT / 'validation/offline-test-tmp')
        self.data = Path(self.temp.name)
        self.pending = self.data / 'pending' / self.BVID
        (self.pending / 'downloads' / '000').mkdir(parents=True)
        self.video = self.pending / 'downloads' / '000' / 'source.mp4'
        self.video.write_bytes(b'fake video bytes')

    def tearDown(self):
        self.temp.cleanup()

    def test_platform_subtitles_are_used_directly(self):
        captions = self.video.with_suffix('.zh.srt')
        captions.write_text('1\n00:00:00,000 --> 00:00:01,000\n你好\n', encoding='utf-8')
        with patch.object(p, 'prepare', return_value=Path('job-from-captions')) as prepare:
            result = ingest.build_job(self.data, self.BVID, self.video, 'small')
        self.assertEqual(result, Path('job-from-captions'))
        self.assertEqual(prepare.call_args[0][3], captions)  # 传进去的就是平台字幕

    def test_retry_resumes_transcript_job_without_preparing_again(self):
        job = self.pending / '3a48b3fcd6623f542b39'
        p.write(job / 'state.json', {'status': 'paused'})
        p.write(job / 'calls/000/call.json', {'status': 'complete'})
        p.write(job / 'calls/001/call.json', {'status': 'failed'})
        p.write(self.pending / 'asr-cache/state.json', {'status': 'complete'})
        with p.use_source(self.data, self.BVID), \
                patch.object(ingest, 'build_job') as prepare, \
                patch.object(p, 'analyze') as analyze, \
                patch.object(p, 'publish', return_value=job):
            self.assertEqual(ingest._run(self.data, self.BVID, True, None, 'small', retry=True), job)
        prepare.assert_not_called()
        analyze.assert_called_once_with(job, None)
        self.assertTrue((job / 'calls/000/call.json').exists())
        self.assertEqual(len(list((job / 'call-history').glob('*/call.json'))), 1)

    def test_discovery_includes_both_job_types_but_not_processing_caches(self):
        names = ['3a48b3fcd6623f542b39', 'visual-e337394590c6ab3c',
                 'asr-abc-small', 'ocr-abc', 'audio-abc']
        for name in names:
            p.write(self.pending / name / 'state.json', {'status': 'paused'})
        self.assertEqual({path.parent.name for path in p.analysis_states(self.pending)}, set(names[:2]))

    def test_speech_without_subtitles_goes_to_local_transcription(self):
        with patch.object(ingest, 'audio_evidence',
                          return_value=(self.pending / 'audio-x',
                                        {'speech_intervals': [{'start': 0, 'end': 3}], 'duration': 120.0})), \
             patch('snakekb.transcribe.transcribe', return_value=Path('asr-captions.json')) as transcribe, \
             patch.object(p, 'prepare', return_value=Path('job-from-asr')) as prepare:
            result = ingest.build_job(self.data, self.BVID, self.video, 'medium')
        self.assertEqual(result, Path('job-from-asr'))
        transcribe.assert_called_once()
        self.assertEqual(transcribe.call_args[0][1], 'medium')  # 模型大小透传下去
        self.assertEqual(prepare.call_args[0][3], Path('asr-captions.json'))

    def test_silence_falls_back_to_screen_text(self):
        ocr = self.pending / 'ocr-abc'
        ocr.mkdir()
        p.write(ocr / 'state.json', {'status': 'extracted_not_formal'})
        with patch.object(ingest, 'audio_evidence',
                          return_value=(self.pending / 'audio-x',
                                        {'speech_intervals': [], 'duration': 60.0})), \
             patch.object(ingest, 'extract', return_value=ocr), \
             patch.object(ingest, 'prepare', return_value=Path('job-from-ocr')) as prepare:
            result = ingest.build_job(self.data, self.BVID, self.video, 'small')
        self.assertEqual(result, Path('job-from-ocr'))
        self.assertEqual(prepare.call_args[0][0], ocr)
        self.assertTrue(prepare.call_args[1]['music_only'])

    def test_long_silent_video_reaches_ocr_without_duration_cap(self):
        ocr = self.pending / 'ocr-long'
        ocr.mkdir()
        p.write(ocr / 'state.json', {'status': 'extracted_not_formal'})
        with patch.object(ingest, 'audio_evidence', return_value=(self.pending / 'audio-x', {'speech_intervals': [], 'duration': 3600.0})), patch.object(ingest, 'extract', return_value=ocr) as extract, patch.object(ingest, 'prepare', return_value=Path('long-job')):
            self.assertEqual(ingest.build_job(self.data, self.BVID, self.video, 'small'), Path('long-job'))
            extract.assert_called_once_with(self.video)


if __name__ == '__main__':
    unittest.main()
