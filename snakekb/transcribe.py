"""Local ASR fallback; audio is never sent to a cloud transcription service."""
import argparse
import os
from pathlib import Path
import time
import wave

from . import pipeline as p


def transcribe(video, model='small', resume_local=False):
    video = Path(video).resolve()
    if not video.is_file():
        raise p.Paused(f'找不到这个视频文件：{video}。确认路径写对了，或者先把视频下载到本地。')
    folder = p.initialize(p.DEFAULT_DATA) / ('asr-' + p.digest(video)[:20] + '-' + model)
    folder.mkdir(exist_ok=True)
    state_file = folder / 'state.json'
    if state_file.exists():
        old = p.load(state_file)
        if old['status'] == 'complete':
            return folder / 'captions.json'
        if old['status'] != 'paused' or not resume_local:
            raise p.Paused(f'这个视频之前转过一次，停在了「{old["status"]}」这一步，不会自动重跑。'
                           f'先看 {folder} 里的记录；确认要接着跑就加 --resume。')
        p.write(folder / f"history-{time.time_ns()}.json", old)
    else:
        with state_file.open('x', encoding='utf-8') as f:
            f.write('{"status":"started"}')
    state = {'status': 'started', 'started': time.time(), 'model': model,
             'engine': 'faster-whisper', 'device': 'cpu', 'compute_type': 'int8',
             'video_sha256': p.digest(video), 'audio_uploaded': False,
             'provenance': 'local_machine_transcription_not_platform_subtitles'}
    p.write(state_file, state)
    try:
        audio = folder / 'audio.wav'
        if not audio.exists():
            p.run_media([p.tool('ffmpeg'), '-nostdin', '-hide_banner', '-xerror',
                         '-protocol_whitelist', 'file,pipe', '-i', str(video), '-vn',
                         '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(audio)], folder / 'audio.log')
        os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
        os.environ.setdefault('HF_HUB_DISABLE_IMPLICIT_TOKEN', '1')
        os.environ.setdefault('HF_HUB_DOWNLOAD_TIMEOUT', '30')
        from faster_whisper import WhisperModel
        import numpy as np
        with wave.open(str(audio), 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                raise p.Paused('转写用的音轨格式不对：需要单声道、16kHz、16 位 PCM。用 ffmpeg 转成这个格式再跑：'
                               'ffmpeg -i 视频 -vn -ac 1 -ar 16000 -c:a pcm_s16le 音频.wav')
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
        engine = WhisperModel(model, device='cpu', compute_type='int8', cpu_threads=8,
                              download_root=str(p.ROOT / '.models'))
        segments, info = engine.transcribe(samples, language='zh', beam_size=5,
                                           vad_filter=False, condition_on_previous_text=False)
        rows = []
        for s in segments:
            rows.append({'start': s.start, 'end': s.end, 'text': s.text,
                         'avg_logprob': s.avg_logprob, 'no_speech_prob': s.no_speech_prob})
            p.write(folder / 'partial.json', rows)
            state.update(processed_seconds=s.end, duration=info.duration)
            p.write(state_file, state)
            acquisition = folder.parent / 'acquisition.json'
            progress = p.load(acquisition)
            progress.update(status='ingesting', reasons=[f'正在本地转写语音：{s.end:.0f} / {info.duration:.0f} 秒，完成后自动继续分析。'])
            p.write(acquisition, progress)
            print(f'Transcribed {s.end:.1f}s', flush=True)
        if not rows:
            raise p.Paused('整段音频里没识别出人声，不能当字幕用。如果这本来就是纯音乐视频，改用画面文字提取那条路径。')
        p.parse_captions(folder / 'partial.json', info.duration)
        p.write(folder / 'captions.json', {'segments': rows, 'provenance': state['provenance']})
        state.update(status='complete', duration=info.duration, segments=len(rows), finished=time.time())
        p.write(state_file, state)
        return folder / 'captions.json'
    except Exception as error:
        state.update(status='paused', error_type=type(error).__name__, reason=str(error), finished=time.time())
        p.write(state_file, state)
        raise p.Paused('本地转写中途停下了（' + type(error).__name__ + '）。'
                       f'具体原因已写进 {state_file}，处理完加 --resume 接着跑，已转好的部分不会丢。') from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--video', required=True)
    parser.add_argument('--model', choices=['base', 'small', 'medium'], default='small')
    parser.add_argument('--resume-local', action='store_true')
    args = parser.parse_args()
    try:
        print(transcribe(args.video, args.model, args.resume_local))
    except p.Paused as error:
        print(error)
        raise SystemExit(2)
