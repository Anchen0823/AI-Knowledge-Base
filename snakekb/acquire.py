"""受控的 yt-dlp 下载：不给重试、不保存凭据和带签名的下载地址。"""
import argparse
from contextlib import closing
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import sqlite3

from . import pipeline as p


def cookie_jar(path):
    path = Path(path).resolve()
    jar = http.cookiejar.MozillaCookieJar()
    jar.load(str(path), ignore_discard=True, ignore_expires=False)
    # 只用指定站点自己的 Cookie，不读浏览器。
    for c in list(jar):
        if c.domain.lstrip('.') != 'bilibili.com' and not c.domain.endswith('.bilibili.com'):
            jar.clear(c.domain, c.path, c.name)
    if not list(jar):
        raise p.Paused('这个 Cookie 文件里没有未过期的 B 站 Cookie。请重新导出一份再试。')
    return jar


class QuietLogger:
    def debug(self, message):
        pass

    info = warning = error = debug


def chrome_jar(ydl):
    """Read only Bilibili rows; use yt-dlp's normal user-session decryption."""
    from yt_dlp.cookies import YDLLogger, YoutubeDLCookieJar, get_cookie_decryptor, _process_chrome_cookie
    root = Path(os.environ['LOCALAPPDATA']) / 'Google/Chrome/User Data'
    databases = [root / 'Default/Network/Cookies']
    databases += sorted(root.glob('Profile */Network/Cookies'))
    logger = YDLLogger(ydl)
    for database in databases:
        if not database.is_file():
            continue
        try:
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
                version = int(conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])
                conn.text_factory = bytes
                rows = conn.execute(
                    'SELECT host_key,name,value,encrypted_value,path,expires_utc,is_secure '
                    "FROM cookies WHERE host_key='bilibili.com' OR host_key LIKE '%.bilibili.com'").fetchall()
        except sqlite3.Error:
            raise p.Paused('Chrome 的 Cookie 数据库被占用或读不了。正常退出 Chrome 后重试。') from None
        if not rows:
            continue
        decryptor = get_cookie_decryptor(str(root), 'Chrome', logger, meta_version=version)
        jar = YoutubeDLCookieJar()
        for row in rows:
            _, cookie = _process_chrome_cookie(decryptor, *row)
            if cookie and not cookie.is_expired():
                jar.set_cookie(cookie)
        if any(c.name == 'SESSDATA' for c in jar):
            return jar
    raise p.Paused('Chrome 里没找到可用的 B 站登录 Cookie。可能是没登录，或 Cookie 被系统加密保护。')


def safe_error(error):
    """存错误原因时不带原文：下载地址里可能含签名令牌。"""
    text = str(error)
    if re.search(r'ffmpeg.*(?:not installed|not found)|ffprobe.*not found', text, re.I):
        return '找不到 FFmpeg，无法合并音视频。请安装 FFmpeg 并加入 PATH 后重新导入'
    if 'not a valid URL' in text:
        return '下载器收到的不是完整视频地址，请使用 https://www.bilibili.com/video/BV…/ 链接'
    if re.search(r'ProxyError|Unable to connect to proxy', text, re.I):
        return '连接代理失败，请检查本机代理是否运行及端口设置后重试'
    if re.search(r'timed out|TimeoutError', text, re.I):
        return '连接视频平台超时，请检查网络后重试'
    if re.search(r'DPAPI|decrypt|app.bound', text, re.I):
        return 'Chrome 的登录 Cookie 无法由当前用户进程解密；没有改动浏览器的安全设置，可改用导出的 Cookie 文件'
    code = re.search(r'HTTP Error (\d{3})', text)
    if code:
        return '平台返回 HTTP ' + code[1] + '，已暂停且不自动重试'
    if re.search(r'login|log in|sign in|cookies|会员|登录', text, re.I):
        return '平台要求有效登录或权限。正常登录后导出 Cookie 文件，再用 --cookies-file 传入'
    return '下载没有完成（' + type(error).__name__ + '）。原始错误可能含凭据，未保存'


def worker(folder, url, cookies=None, browser=None):
    folder = Path(folder).resolve()
    data = folder.parents[3]
    bvid = folder.parents[1].name
    with p.use_source(data, bvid):
        return _worker(folder, p.current().url, cookies, browser)


def _worker(folder, url, cookies=None, browser=None):
    record = p.load(folder / 'attempt.json')
    try:
        import yt_dlp
        options = {
            'ffmpeg_location': str(Path(p.tool('ffmpeg')).parent),
            'outtmpl': str(folder / 'source.%(ext)s'),
            'format': 'bv*[height<=1080]+ba/b[height<=1080]',
            'merge_output_format': 'mp4', 'noplaylist': True,
            'writesubtitles': True, 'writeautomaticsub': True,
            'subtitleslangs': ['zh.*', 'ai-zh.*'],
            'subtitlesformat': 'srt/vtt/json',
            'retries': 0, 'fragment_retries': 0, 'extractor_retries': 0,
            'socket_timeout': 20, 'quiet': True, 'no_warnings': True,
            'logger': QuietLogger(), 'noprogress': True,
            'max_filesize': 500_000_000,
            'skip_download': bool(record.get('reused_video')),
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            if cookies:
                ydl.cookiejar = cookie_jar(cookies)
            elif browser == 'chrome':
                ydl.cookiejar = chrome_jar(ydl)
            info = ydl.extract_info(url, download=False)
            if info.get('_type') in {'playlist', 'multi_video'}:
                raise p.Paused('这个链接解析成了合集或多个分 P。这里一次只处理一条视频：'
                               '打开其中那一集，复制它自己的视频地址再试。')
            found = str(info.get('id') or '')
            profile = p.current()
            if found != profile.bvid:
                raise p.Paused(f'下载器解析出的是 {found or "未知视频"}，和这条来源登记的 {profile.bvid} 不一样，先停下。'
                               '如果链接贴错了，换回正确的那条；如果是短链指向了别的视频，请直接用视频页地址。')
            duration = info.get('duration')
            if profile.duration and duration and abs(float(duration) - profile.duration) > 2:
                raise p.Paused(f'下载器报告的时长是 {float(duration):.1f} 秒，和登记的 {profile.duration:.1f} 秒对不上，'
                               '可能是片段或另一条视频。')
            p.write(folder / 'metadata.json', {k: info.get(k) for k in
                    ('id', 'title', 'uploader', 'duration', 'description')})
            ydl.process_info(info)
        videos = [f for f in folder.glob('source.*') if f.suffix in {'.mp4', '.mkv', '.webm'} and '.f' not in f.stem]
        captions = [f for f in folder.glob('source.*') if f.suffix in {'.srt', '.vtt', '.json'}]
        record.update(video=videos[0].name if len(videos) == 1 else None,
                      captions=captions[0].name if captions else None)
        record.update(status='acquired' if record['video'] and record['captions'] else 'paused',
                      reason=None if record['video'] and record['captions'] else
                      ('拿到了视频，但平台没给中文字幕。接下来会改用本地语音转写或画面文字，'
                       '素材留着不算浪费。'))
    except Exception as error:
        record.update(status='paused', reason=str(error) if isinstance(error, p.Paused) else safe_error(error))
    record['finished'] = time.time()
    p.write(folder / 'attempt.json', record)


def resolve(url):
    """把 b23.tv 短链展开成 BV 号：只解析，不下载。失败会说明怎么绕开。"""
    with tempfile.TemporaryDirectory(prefix='snakekb-resolve-') as temp:
        target = Path(temp) / 'info.json'
        args = [sys.executable, '-m', 'snakekb.acquire', '--resolve', url, '--out', str(target)]
        try:
            done = subprocess.run(args, cwd=p.ROOT, capture_output=True, timeout=None)
        except subprocess.TimeoutExpired:
            raise p.Paused('展开短链超过 2 分钟没有结果，已停止。可以打开短链，'
                           '把地址栏里的完整视频地址复制过来。') from None
        if not target.exists():
            raise p.Paused('短链展开失败。可以打开它，把地址栏里的 https://www.bilibili.com/video/BV…/ 复制过来。'
                           '（下载器退出码 ' + str(done.returncode) + '）')
        return p.load(target)


def resolve_worker(url, out):
    import yt_dlp
    options = {'quiet': True, 'no_warnings': True, 'noplaylist': True,
               'skip_download': True, 'socket_timeout': 20,
               'retries': 0, 'extractor_retries': 0, 'logger': QuietLogger()}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get('_type') in {'playlist', 'multi_video'}:
        raise p.Paused('这个短链指向合集或多个分 P，这里一次只处理一条视频。')
    found = str(info.get('id') or '')
    if not re.fullmatch(r'BV[1-9A-HJ-NP-Za-km-z]{10}', found):
        raise p.Paused('短链展开后拿到的不是 B 站单条视频。')
    p.write(Path(out), {'id': found, 'title': info.get('title'),
                        'duration': info.get('duration'),
                        'webpage_url': info.get('webpage_url')})


def acquire(data, source, cookies=None, retry=False, browser=None):
    """下载一条视频的素材。短链会先展开，新来源下载成功后自动登记。"""
    info = p.identify(source, data)
    with p.use_source(data, info['bvid']):
        return _acquire(data, source, cookies, retry, browser)


def _acquire(data, source, cookies, retry, browser):
    if cookies and browser:
        raise p.Paused('Cookie 文件和 Chrome 登录态只能选一个。')
    if cookies:
        cookie_jar(cookies)  # 先验证凭据，避免白跑一次网络请求。
    root = p.initialize(data) / 'downloads'
    root.mkdir(exist_ok=True)
    previous = sorted(root.glob('*/attempt.json'))
    for path in previous:
        record = p.load(path)
        if record['status'] == 'acquired':
            return path.parent
        if record['status'] == 'started':
            raise p.Paused('有一次下载可能还在跑（结果不确定）。先确认它已经结束，再决定是否重试。')
    if previous and not retry:
        raise p.Paused('上次下载停在中途。补好材料后加 --retry-acquisition 再试，历史记录会保留。')
    folder = root / f'{len(previous):03}'
    folder.mkdir()
    reused_video = None
    for path in reversed(previous):
        old = p.load(path)
        name = old.get('video')
        if name and Path(name).name == name and (path.parent / name).is_file():
            shutil.copy2(path.parent / name, folder / name)
            reused_video = name
            break
    p.write(folder / 'attempt.json', {'status': 'started', 'started': time.time(),
            'authenticated': bool(cookies or browser), 'automatic_retries': 0, 'timeout_seconds': None,
            'reused_video': reused_video})
    args = [sys.executable, '-m', 'snakekb.acquire', '--worker', str(folder), '--url', p.current().url]
    if cookies:
        args += ['--cookies-file', str(Path(cookies).resolve())]
    if browser:
        args += ['--cookies-from-browser', browser]
    process = subprocess.Popen(args, cwd=p.ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        process.wait()
    except subprocess.TimeoutExpired:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15)
        else:
            process.kill()
        process.wait()
        p.write(folder / 'attempt.json', {'status': 'paused', 'reason': '下载超过 10 分钟还没结束，已停止且不自动重试'})
    record = p.load(folder / 'attempt.json')
    state = p.load(p.initialize(data) / 'acquisition.json')
    state.update(status=record['status'], download_attempts=len(previous)+1,
                 reasons=[record.get('reason') or '音视频和字幕已经拿到，接下来做音画覆盖检查'],
                 latest_download=str(folder.relative_to(data)))
    p.write(p.initialize(data) / 'acquisition.json', state)
    if record['status'] != 'acquired':
        raise p.Paused(record.get('reason') or '下载子进程中断了，结果不确定，先停下。')
    p.remember_from_metadata(data)  # 新来源在这里拿到标题和真实时长
    return folder


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='下载一条视频的素材（内部使用）')
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--url')
    parser.add_argument('--resolve')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--cookies-file')
    parser.add_argument('--cookies-from-browser', choices=['chrome'])
    args = parser.parse_args()
    if args.resolve:
        if not args.out:
            raise SystemExit('--resolve 需要同时给 --out')
        try:
            resolve_worker(args.resolve, args.out)
        except p.Paused as error:
            print(error, file=sys.stderr)
            raise SystemExit(2)
    elif args.worker and args.url:
        worker(args.worker, args.url, args.cookies_file, args.cookies_from_browser)
    else:
        parser.error('需要 --worker 加 --url，或者 --resolve 加 --out')
