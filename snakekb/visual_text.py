"""在本机逐秒提取画面文字。注意：这不是语音字幕。"""
import argparse
from pathlib import Path
import time

from . import pipeline as p


def extract(video, resume=False):
    video = Path(video).resolve()
    folder = p.initialize(p.DEFAULT_DATA) / ('ocr-' + p.digest(video)[:20])
    folder.mkdir(exist_ok=True)
    frames = folder / 'frames'
    frames.mkdir(exist_ok=True)
    state_file = folder / 'state.json'
    if state_file.exists() and not resume:
        raise p.Paused('上次的画面提取留下了记录，不会自动覆盖。确认已有文件没问题后，加 --resume 接着跑。')
    p.write(state_file, {'status': 'extracting', 'started': time.time(),
                        'provenance': 'local_screen_ocr_not_audio_transcript'})
    try:
        if not (folder / 'frames.log').exists():
            p.run_media([p.tool('ffmpeg'), '-nostdin', '-hide_banner', '-xerror',
                     '-protocol_whitelist', 'file,pipe', '-i', str(video), '-an',
                     '-vf', "select='isnan(prev_selected_t)+gte(t-prev_selected_t,1)',showinfo",
                     '-fps_mode', 'vfr', '-q:v', '3', str(frames / '%04d.jpg')],
                    folder / 'frames.log', timeout=None)
        import re
        log = (folder / 'frames.log').read_text(encoding='utf-8', errors='replace')
        times = [float(m[1]) for m in re.finditer(r'\bn:\s*\d+\s+pts:.*?pts_time:([\d.]+)', log)]
        files = sorted(frames.glob('*.jpg'))
        if len(files) != len(times):
            raise p.Paused(f'抽到 {len(files)} 张画面、{len(times)} 个时间戳，对不上；先停下，不用这些结果。')
        from rapidocr import RapidOCR
        engine = RapidOCR(params={'EngineConfig.onnxruntime.intra_op_num_threads': 4,
                                  'EngineConfig.onnxruntime.inter_op_num_threads': 1})
        rows = p.load(folder / 'screen-text.json') if (folder / 'screen-text.json').exists() else []
        completed = len(rows)
        import cv2
        p.write(state_file, {'status': 'recognizing', 'frames': len(files)})
        for index, (file, timestamp) in enumerate(zip(files, times)):
            if index < completed:
                continue
            pixels = cv2.imread(str(file))
            offset = 0 if index % 5 == 0 else 900
            result = engine(pixels[offset:, :])
            texts = list(result.txts) if result.txts is not None else []
            scores = list(map(float, result.scores)) if result.scores is not None else []
            boxes = result.boxes.tolist() if result.boxes is not None else []
            for box in boxes:
                for point in box:
                    point[1] += offset
            rows.append({'time': timestamp, 'file': 'frames/' + file.name,
                         'texts': texts, 'scores': scores, 'boxes': boxes,
                         'region': 'full_frame' if offset == 0 else 'bottom_subtitles'})
            p.write(folder / 'screen-text.json', rows)
            if index % 20 == 0:
                print(f'OCR {index + 1}/{len(files)} ({timestamp:.1f}s)', flush=True)
        p.write(state_file, {'status': 'extracted_not_formal', 'frames': len(files),
                'provenance': 'local_screen_ocr_not_audio_transcript',
                'audio_content_confirmed': False, 'finished': time.time(),
                'limitations': ['1 second sampling may miss brief text',
                                'Full screen OCR every 5 samples, bottom captions each sample',
                                'OCR must be checked against images; not semantic understanding']})
        return folder
    except Exception as error:
        p.write(state_file, {'status': 'paused', 'error_type': type(error).__name__})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--video', required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    print(extract(args.video, args.resume))
