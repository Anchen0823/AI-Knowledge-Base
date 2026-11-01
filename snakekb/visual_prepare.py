"""把「只有背景音乐、讲解在画面文字上」的视频整理成可分析的任务。

这里只做本地搬运和核对：从逐秒 OCR 记录里挑出字幕条，按 5 秒挑一张截图，
再写一份覆盖说明。任何一处对不上都停下，不猜、不补齐。
"""
import argparse
from pathlib import Path
import shutil

from . import pipeline as p

VIDEO_SUFFIXES = {'.mp4', '.mkv', '.webm', '.mov'}


def locate_video(pending, prefix):
    """按内容哈希找原始视频，不假定它一定在 downloads/000。"""
    for candidate in sorted(pending.glob('downloads/*/source.*')):
        if candidate.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        digest = p.digest(candidate)
        if digest[:20] == prefix:
            return candidate, digest
    return None, None


def prepare(folder, music_only=False):
    folder = Path(folder).resolve()
    if folder.parent != p.DEFAULT_DATA / 'pending' / p.BVID:
        raise p.Paused('画面材料的目录不对：它必须放在 data/pending/<视频ID>/ 下面。')
    if not music_only:
        raise p.Paused('这条路径只用于「只有背景音乐、讲解都在画面文字上」的视频。确认后加 --confirm-music-only。')
    state = p.load(folder / 'state.json')
    if state['status'] != 'extracted_not_formal':
        raise p.Paused(f"全片画面文字还没提取完（当前状态：{state['status']}）。先让提取跑完再准备。")
    rows = p.load(folder / 'screen-text.json')
    times = [row['time'] for row in rows]
    if not folder.name.startswith('ocr-'):
        raise p.Paused('画面材料目录名不是 ocr-<哈希>，看不出它对应哪条视频。')
    prefix = folder.name[len('ocr-'):]
    video, video_hash = locate_video(folder.parent, prefix)
    if video is None:
        raise p.Paused(f'找不到和这份画面材料对应的原始视频（哈希 {prefix}）。素材可能已被清理，需要重新下载。')
    audio_folder = folder.parent / ('audio-' + video_hash[:20])
    if (audio_folder / 'vad.json').exists():
        vad = p.load(audio_folder / 'vad.json')
        if not vad.get('full_audio_decode_ok'):
            raise p.Paused('没有音轨完整解码的记录，无法确认这是纯背景音乐。')
        decode = {'full_audio_video_decode_exit_code': 0,
                  'evidence': '音频解码完成，画面抽取到片尾',
                  'video_sha256': video_hash}
    else:
        vad = p.load(folder.parent / ('asr-' + video_hash[:20] + '-small') / 'vad.json')
        decode = p.load(p.ROOT / 'validation/evidence/full-media-decode.json')
    if vad['speech_intervals'] or decode.get('full_audio_video_decode_exit_code') != 0:
        raise p.Paused('音轨里检测到人声，或全片解码没成功。这条路径只处理纯背景音乐的视频。')
    duration = vad['duration']
    if not times or times != sorted(set(times)) or max(b-a for a,b in zip([0]+times, times+[duration])) > 1.15:
        raise p.Paused('画面不是每秒一张，中间有缺口，覆盖不完整。')
    revision = 'visual-' + p.digest(folder / 'screen-text.json')[:16]
    job = folder.parent / revision
    if job.exists():
        return job
    job.mkdir()
    (job / 'frames').mkdir()
    captions = []
    frames = []
    for index, row in enumerate(rows):
        if row['texts']:
            captions.append({'id': f's{len(captions):04}', 'start': row['time'],
                'end': min(row['time'] + .01, duration),
                'text': '\n'.join(row['texts']), 'ocr_scores': row['scores'],
                'evidence_type': 'screen_text_observed_at_sample_not_speech',
                'region': row.get('region', 'full_frame')})
        if index % 5 == 0 or index == len(rows)-1:
            source = folder / row['file']
            if source.parent != folder / 'frames' or not source.is_file():
                raise p.Paused('有截图缺失，或路径跑到了素材目录之外。')
            dest = job / 'frames' / source.name
            shutil.copyfile(source, dest)
            frames.append({'id': f'f{len(frames):04}', 'time': row['time'],
                           'file': 'frames/' + dest.name, 'sha256': p.digest(dest)})
    p.write(job / 'transcript.json', captions)
    p.write(job / 'frames.json', frames)
    p.write(job / 'audio-evidence.json', {'video_sha256': video_hash,
            'user_confirmed_music_only': True, 'vad': vad, 'decode': decode})
    p.write(job / 'coverage.json', {'duration': duration,
            'full_audio_decode_ok': True, 'full_video_decode_ok': True,
            'audio_mode': 'user_confirmed_music_only', 'user_confirmation': True,
            'unexplained_audio_gaps': [], 'caption_gaps': [],
            'caption_gap_interpretation': '这些是画面文字采样点，不代表语音时间轴',
            'screen_samples': len(rows), 'max_frame_gap': max(b-a for a,b in zip([0]+[f['time'] for f in frames], [f['time'] for f in frames]+[duration])),
            'limitations': '逐秒 OCR 和每 5 秒一张截图都可能漏掉极短画面；模型必须报告不确定'})
    p.write(job / 'state.json', {**p.source_state(), 'status': 'prepared',
            'revision': revision, 'provenance': 'platform_export',
            'caption_provenance': 'local_screen_ocr_not_audio_transcript',
            'audio_mode': 'user_confirmed_music_only', 'source_materials': folder.name,
            'video_sha256': video_hash,
            'duration': duration, 'frames': len(frames), 'captions': len(captions)})
    return job


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='把已提取的画面文字整理成可分析的任务')
    parser.add_argument('folder', type=Path, help='data/pending/<视频ID>/ocr-<哈希> 目录')
    parser.add_argument('--confirm-music-only', action='store_true', help='确认视频里只有背景音乐')
    args = parser.parse_args()
    print(prepare(args.folder, args.confirm_music_only))
