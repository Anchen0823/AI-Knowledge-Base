from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import wave
from unittest.mock import patch

from snakekb import pipeline as p
from snakekb.transcribe import transcribe


class TranscriptionTests(unittest.TestCase):
    def test_screen_text_requires_audio_evidence(self):
        base = p.ROOT / 'validation/offline-test-tmp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            job = Path(temp)
            p.write(job / 'state.json', {'status': 'prepared',
                    'caption_provenance': 'local_screen_ocr_not_audio_transcript'})
            p.write(job / 'frames.json', [])
            p.write(job / 'coverage.json', {'full_audio_decode_ok': True,
                    'full_video_decode_ok': True, 'max_frame_gap': 5})
            # 提示语会重写，测试只锁住「必须提到纯背景音乐这个前提」。
            self.assertTrue(any('背景音乐' in r for r in p.input_gate(job)))

    def test_uncertain_asr_cannot_pass_gate(self):
        base = p.ROOT / 'validation/offline-test-tmp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            job = Path(temp)
            p.write(job / 'state.json', {'status': 'prepared'})
            p.write(job / 'frames.json', [])
            p.write(job / 'coverage.json', {'full_audio_decode_ok': True,
                    'full_video_decode_ok': True, 'max_frame_gap': 5,
                    'asr_uncertain_intervals': [[20, 25]]})
            self.assertTrue(any('低置信度' in r for r in p.input_gate(job)))

    def test_timestamped_machine_provenance_and_cached_resume(self):
        base = p.ROOT / 'validation/offline-test-tmp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            root = Path(temp)
            video = root / 'input.mp4'
            video.write_bytes(b'fake-media-for-unit-test')
            segment = SimpleNamespace(start=0.0, end=2.0, text='测试字幕',
                                      avg_logprob=-0.2, no_speech_prob=0.01)
            engine = SimpleNamespace(transcribe=lambda *a, **kw: (iter([segment]), SimpleNamespace(duration=2.0)))
            module = SimpleNamespace(WhisperModel=lambda *a, **kw: engine)
            def make_audio(args, log):
                with wave.open(args[-1], 'wb') as wav:
                    wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                    wav.writeframes(b'\x00\x00' * 32000)
            with patch.dict('sys.modules', {'faster_whisper': module}), \
                 patch.object(p, 'DEFAULT_DATA', root / 'data'), \
                 patch.object(p, 'ROOT', root), \
                 patch.object(p, 'tool', return_value='ffmpeg'), \
                 patch.object(p, 'run_media', side_effect=make_audio) as media:
                output = transcribe(video)
                result = p.load(output)
                self.assertEqual(result['segments'][0]['end'], 2.0)
                self.assertEqual(result['provenance'], 'local_machine_transcription_not_platform_subtitles')
                self.assertEqual(transcribe(video), output)
                media.assert_called_once()
                self.assertFalse(p.load(output.parent / 'state.json')['audio_uploaded'])
