"""一条龙导入：下载 -> 判断材料形态 -> 生成任务 -> 图文分析 -> 发布。

字幕从哪来，按素材自己判断，顺序是：
  1. 平台自带中文字幕 —— 最准，直接用；
  2. 没有字幕但有人声 —— 本机 faster-whisper 转写，音频不出本机；
  3. 没有人声、只有背景音乐 —— 逐秒识别画面文字。

任何一步失败都会停在待处理区，已取得的材料保留，不自动重试。
"""
import argparse
import os
from pathlib import Path
import wave

from . import pipeline as p
from .acquire import acquire
from .visual_text import extract
from .visual_prepare import prepare



def video_of(pending):
    for candidate in sorted(pending.glob('downloads/*/source.*')):
        if candidate.suffix.lower() in {'.mp4', '.mkv', '.webm', '.mov'}:
            return candidate
    return None


def captions_of(pending, video):
    """平台字幕文件；下载目录里没有就返回 None。"""
    for candidate in sorted(video.parent.glob('source.*')):
        if candidate.suffix.lower() in {'.srt', '.vtt', '.json'}:
            return candidate
    return None


def audio_evidence(pending, video):
    """提取音轨并检测人声。返回 (音轨目录, vad 记录)。"""
    folder = pending / ('audio-' + p.digest(video)[:20])
    folder.mkdir(exist_ok=True)
    if (folder / 'vad.json').exists():
        return folder, p.load(folder / 'vad.json')
    wav = folder / 'audio.wav'
    if wav.exists():
        raise p.Paused('上次提取音轨中断了，留下的 audio.wav 不会自动覆盖。'
                       f'确认后手动删掉 {wav} 再重跑。')
    p.run_media([p.tool('ffmpeg'), '-nostdin', '-hide_banner', '-xerror',
                 '-protocol_whitelist', 'file,pipe', '-i', str(video), '-vn',
                 '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)], folder / 'audio.log')
    import numpy as np
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    with wave.open(str(wav), 'rb') as handle:
        samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype='<i2').astype(np.float32) / 32768
    spans = get_speech_timestamps(samples, VadOptions(threshold=.5))
    vad = {'duration': len(samples) / 16000,
           'speech_intervals': [{'start': s['start'] / 16000, 'end': s['end'] / 16000} for s in spans],
           'method': 'Silero VAD threshold 0.5', 'full_audio_decode_ok': True}
    p.write(folder / 'vad.json', vad)
    return folder, vad


def build_job(data, bvid, video, asr_model):
    """按素材形态挑一条路，生成可分析的任务目录。"""
    pending = video.parent.parent
    captions = captions_of(pending, video)
    if captions:
        print('发现平台字幕，按字幕核对。', flush=True)
        return p.prepare(data, bvid, video, captions, 'platform_export')
    _, vad = audio_evidence(pending, video)
    if vad['speech_intervals']:
        print('没有平台字幕，但检测到人声：改用本机语音转写。这一步可能要几分钟…', flush=True)
        from .transcribe import transcribe
        return p.prepare(data, bvid, video, transcribe(video, asr_model), 'platform_export')
    print('没有人声，按画面文字提取。这一步比较慢…', flush=True)
    folder = extract(video)
    if p.load(folder / 'state.json')['status'] != 'extracted_not_formal':
        raise p.Paused('上次的画面文字识别没做完。到待处理页看状态，不要重复启动。')
    return prepare(folder, music_only=True)


def run(source, allow_cloud=False, proxy=None, asr_model='small', retry=False):
    info = p.identify(source)
    data = p.DEFAULT_DATA
    if info['kind'] == 'opus':
        from .articles import ingest
        return ingest(data, info, allow_cloud, proxy)
    with p.use_source(data, info['bvid']):
        return _run(data, info['bvid'], allow_cloud, proxy, asr_model, retry)


def _run(data, bvid, allow_cloud, proxy, asr_model, retry=False):
    index = data / 'formal/index.json'
    if index.exists() and bvid in p.load(index):
        p.query(data, bvid)
        entry = data / 'formal' / p.load(index)[bvid]
        print('这条视频已经入库，直接复用：' + str(entry), flush=True)
        return entry
    if not allow_cloud:
        raise p.Paused('完整导入需要把截图和画面文字交给 Codex 分析，请先明确同意（--allow-cloud-analysis）。')
    pending = p.initialize(data)
    status = p.load(pending / 'acquisition.json')
    status.update(status='ingesting', reasons=[])
    p.write(pending / 'acquisition.json', status)

    existing = p.analysis_states(pending)
    if existing:
        state = p.load(existing[-1])
        if state['status'] == 'analyzing':
            raise p.Paused('上一次分析可能还在跑，先等它结束；不要重复启动。')
        if state['status'] == 'paused' and not retry:
            raise p.Paused('这个任务之前暂停了。到待处理页看原因，再选择「重新尝试」。')
        job = existing[-1].parent
        if state['status'] == 'paused' and retry:
            if not (job / 'coverage.json').exists():
                video = video_of(pending)
                candidates = list(pending.glob('asr-*/captions.json'))
                if video:
                    supplied = captions_of(pending, video)
                    if supplied:
                        candidates.insert(0, supplied)
                captions = next((c for c in candidates if p.digest(c) == state.get('captions_sha256')), None)
                if not video or not captions:
                    raise p.Paused('准备阶段中断，找不到匹配的原视频或字幕，请检查保留的素材。')
                job = p.prepare(data, bvid, video, captions, state['provenance'], resume=True)
            else:
                p.reopen_analysis(job)
    else:
        video = video_of(pending)
        if video is None:
            try:
                acquire(data, bvid, retry=retry)
            except p.Paused:
                video = video_of(pending)
                if video is None:
                    raise
            video = video_of(pending) or video
        if video is None:
            raise p.Paused('下载记录显示成功，但目录里找不到视频文件。检查 data/pending 下这条视频的下载目录。')
        status.update(status="ingesting", reasons=["正在本地转写语音或提取画面文字，完成后自动继续分析。"] )
        p.write(pending / "acquisition.json", status)
        try:
            job = build_job(data, bvid, video, asr_model)
        except Exception as error:
            status.update(status="paused", reasons=[str(error)])
            p.write(pending / "acquisition.json", status)
            raise

    try:
        p.analyze(job, proxy)
        return p.publish(data, job, proxy)
    except Exception as error:
        state = p.load(job / 'state.json')
        state.update(status='paused', reason=str(error))
        p.write(job / 'state.json', state)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='把一条 B 站视频整理进本地知识库')
    parser.add_argument('--source', default='BV1QTHA6DELc', help='视频链接、b23.tv 短链或 BV 号')
    parser.add_argument('--proxy', default=os.environ.get('SNAKEKB_PROXY', 'http://127.0.0.1:65532'),
                        help='只给模型子进程用的代理')
    parser.add_argument('--no-proxy', action='store_true', help='模型调用不走代理')
    parser.add_argument('--asr-model', choices=['base', 'small', 'medium'], default='small',
                        help='本地语音转写用多大的模型；越大越准也越慢')
    parser.add_argument('--allow-cloud-analysis', action='store_true', help='同意把截图和画面文字交给 Codex')
    parser.add_argument('--retry', action='store_true', help='重新尝试失败的导入，保留历史和已用额度')
    args = parser.parse_args()
    try:
        print(run(args.source, args.allow_cloud_analysis,
                  None if args.no_proxy else args.proxy, args.asr_model, args.retry))
    except (p.Paused, ValueError, OSError) as error:
        bvid = None
        try:
            bvid = p.identify(args.source)['bvid']
        except Exception:
            pass
        if bvid:
            path = p.DEFAULT_DATA / 'pending' / bvid / 'acquisition.json'
            if path.exists():
                state = p.load(path)
                state.update(status='paused_ingest', reasons=[str(error)])
                p.write(path, state)
        print('已暂停：' + str(error))
        print('已经拿到的材料都留在 data/pending 里，没有丢失。按上面的提示处理后可以接着跑，不会从头再来。')
        raise SystemExit(2)
