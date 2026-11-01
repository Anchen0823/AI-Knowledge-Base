"""Readable, explicitly provisional screen-text evidence; never formal notes."""
import argparse
from pathlib import Path
from . import pipeline as p


def render(folder):
    rows = p.load(folder / 'screen-text.json')
    groups = []
    for row in rows:
        # The sample is 1920x1080. Preserve full OCR in the source JSON;
        # this reading aid selects only its bottom subtitle band.
        selected = [(text, score) for text, score, box in
                    zip(row['texts'], row['scores'], row['boxes'])
                    if min(point[1] for point in box) >= 940]
        text = ' '.join(t for t, _ in selected)
        if not text:
            continue
        if groups and groups[-1]['text'] == text and row['time'] - groups[-1]['last'] <= 1.1:
            groups[-1]['last'] = row['time']
            continue
        groups.append({'first': row['time'], 'last': row['time'], 'text': text,
                       'confidence': min(s for _, s in selected), 'file': row['file']})
    lines = ['# 画面字幕摘录（待核查）', '',
             '这是 OCR 提取的画面文字，不是语音转录或已验收知识笔记。',
             '时间表示实际采样观察点，不保证准确的字幕起止或连续覆盖。完整图示文字见 screen-text.json。', '']
    for row in groups:
        lines += [f"- [{row['first']:.1f}–{row['last']:.1f} 秒]({row['file']})：{row['text']}（OCR 置信度 {row['confidence']:.2f}）"]
    (folder / 'screen-captions.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return folder / 'screen-captions.md'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    print(render(parser.parse_args().folder))
