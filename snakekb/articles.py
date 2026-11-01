"""Bilibili opus original-text archival, with optional concept extraction."""
import shutil
import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path
from . import pipeline as p, sources


def parse_html(html, identity):
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*', html)
    if not match:
        raise p.Paused('页面未提供图文正文，可能需要登录或遇到风控。')
    state = json.JSONDecoder().raw_decode(html[match.end():])[0]
    detail = state.get('detail') or {}
    if str(detail.get('id_str')) != identity:
        raise p.Paused('图文页面编号不匹配，已停止收录。')
    modules = detail.get('modules') or []
    if any(m.get('module_paywall') or m.get('module_blocked') for m in modules):
        raise p.Paused('文章存在付费或访问限制，不能确认正文完整。')
    title = detail.get('basic', {}).get('title', '')
    author = ''
    paragraphs = []
    images = []
    def walk(value):
        if isinstance(value, list):
            return ''.join(walk(v) for v in value)
        if not isinstance(value, dict):
            return ''
        if 'words' in value:
            return str(value['words'])
        if value.get('latex_content'):
            return '$' + str(value['latex_content']) + '$'
        if isinstance(value.get('text'), str):
            return value['text']
        url = value.get('url', '')
        if isinstance(url, str) and re.match(r'https?://[^/]*\.hdslb\.com/', url):
            url = re.sub(r'^http:', 'https:', url)
            if url not in images:
                images.append(url)
            return '\n![原文插图](' + url + ')\n'
        return ''.join(walk(v) for v in value.values() if isinstance(v, (dict, list)))
    for module in modules:
        if module.get('module_title'):
            title = module['module_title']['text']
        if module.get('module_author'):
            author = module['module_author'].get('name', '')
        if module.get('module_content'):
            for para in module['module_content'].get('paragraphs', []):
                text = walk(para).strip()
                if text:
                    paragraphs.append(text)
    if not paragraphs:
        raise p.Paused('未解析到正文，未收录空文章。')
    return dict(title=title, author=author, paragraphs=paragraphs, images=images, detail=detail)


def archive_images(article, pending):
    """Keep original images and local OCR evidence, never label OCR as author text."""
    from rapidocr import RapidOCR
    engine = RapidOCR(params={'EngineConfig.onnxruntime.intra_op_num_threads': 4,
                              'EngineConfig.onnxruntime.inter_op_num_threads': 1})
    folder = pending / 'images'
    folder.mkdir(exist_ok=True)
    records = []
    for index, url in enumerate(article['images']):
        file = folder / (f'{index:03d}.img')
        if not file.exists():
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read(30_000_001)
            if len(raw) > 30_000_000:
                raise p.Paused('插图超过大小限制。')
            file.write_bytes(raw)
        record_path = folder / (f'{index:03d}.json')
        if record_path.exists():
            record = p.load(record_path)
        else:
            result = engine(str(file))
            record = {'url': url, 'file': 'images/' + file.name,
                      'texts': list(result.txts) if result.txts is not None else [],
                      'scores': list(map(float, result.scores)) if result.scores is not None else [],
                      'provenance': 'local_image_ocr', 'sha256': p.digest(file)}
            p.write(record_path, record)
        records.append(record)
        print(f'图文插图 OCR {index + 1}/{len(article["images"])}', flush=True)
    p.write(pending / 'image-evidence.json', records)
    return records


def ingest(data, info, allow_cloud=False, proxy=None, html=None):
    data = Path(data)
    key = info['bvid']
    pending = data / 'pending' / key
    pending.mkdir(parents=True, exist_ok=True)
    status = dict(bvid=key, canonical=info['url'], status='ingesting', reasons=[])
    p.write(pending / 'acquisition.json', status)
    try:
        if html is None:
            request = urllib.request.Request(info['url'], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=30) as response:
                html = response.read(10_000_001)
            if len(html) > 10_000_000:
                raise p.Paused('图文页面超过大小限制。')
            html = html.decode('utf-8')
        article = parse_html(html, key[4:])
        p.write(pending / 'article.json', article)
        sources.remember(data, key, title=article['title'])
        revision = hashlib.sha256(json.dumps(article, ensure_ascii=False).encode()).hexdigest()[:20]
        name = key + '-' + revision
        notes = '# ' + article['title'] + '\n\n作者：' + article['author'] + '\n\n原文：' + info['url'] + '\n\n'
        notes += '以下为原文摘录；插图保留远程地址，未做 OCR 或图像解读。\n\n'
        notes += '\n\n'.join('- **原作者陈述**：' + t.replace('\n', '\n  ') for t in article['paragraphs'])
        artifact = None
        image_records = []
        if allow_cloud:
            image_records = archive_images(article, pending)
            notes = notes.replace('插图保留远程地址，未做 OCR 或图像解读。', '插图已本地归档并做 OCR；OCR 可能有误，不代表图像语义解读。')
            notes += '\n\n## 插图文字（本地 OCR，需结合原图核对）\n\n'
            for record in image_records:
                if record['texts']:
                    notes += '- **整理者分析**：插图 OCR 转录（可能有误）：' + ' / '.join(record['texts']).replace('\n', ' ') + '\n  原图：' + record['url'] + '\n'
        if allow_cloud:
            from .concepts import generate
            artifact = generate(data, pending, name, article['title'], notes, proxy)
        entry = data / 'formal' / name
        entry.mkdir(parents=True, exist_ok=True)
        (entry / 'notes.md').write_text(notes, encoding='utf-8')
        p.write(entry / 'article.json', article)
        p.write(entry / 'source.json', dict(bvid=key, canonical=info['url'], title=article['title'], author=article['author'], source_type='opus', published_at=time.time(), provenance='platform_article_text'))
        if image_records:
            shutil.copytree(pending / 'images', entry / 'images', dirs_exist_ok=True)
            p.write(entry / 'image-evidence.json', image_records)
        if artifact:
            p.write(entry / 'concepts.json', artifact)
        p.write(entry / 'gate.json', dict(passed=True, policy='original_text_archive', warnings=['OCR 可能有误；未做图像语义解读'] if image_records else ['插图未做 OCR 或视觉分析']))
        index_path = data / 'formal/index.json'
        index = p.load(index_path) if index_path.exists() else {}
        index[key] = name
        p.write(index_path, index)
        status.update(status='published', title=article['title'])
        return entry
    except Exception as error:
        status.update(status='paused_ingest', reasons=[str(error)])
        raise
    finally:
        p.write(pending / 'acquisition.json', status)
