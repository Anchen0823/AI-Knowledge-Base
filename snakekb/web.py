"""本地私人知识库的网页与接口（只用标准库）。

只监听本机回环地址，除 /、/app.js、/style.css 之外的接口都需要启动时生成的访问口令。
所有报错都走同一条路：Paused 与参数错误返回 400 并带上「发生了什么 + 怎么办」，
未预料的异常返回 500，同时把堆栈打印到启动窗口，方便排查。
"""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import traceback
from urllib.parse import urlsplit, parse_qs
import webbrowser

from . import pipeline as p
from . import sources

DEFAULT_PROXY = 'http://127.0.0.1:65532'

# 前端会直接显示这些句子，所以写成用户能照着做的说法。
NO_TOKEN = '需要访问口令。请用启动窗口打印的完整地址打开页面，或粘贴地址里 #key= 后面那一段。'
BAD_REQUEST = '请求内容不合法，刷新页面可以重试。'
UNEXPECTED = '读取本地资料时出错了。详细信息已打印在启动窗口，可以先刷新页面重试；如果一直失败，请检查 data 目录下的任务记录。'


def snapshot(data):
    """四个视图共用的状态：正式资料、待处理队列、当前任务。这里只读，不建目录。"""
    data = Path(data)
    index_path = data / 'formal/index.json'
    index = p.load(index_path) if index_path.exists() else {}

    library = []
    for bvid, name in index.items():
        if Path(name).name != name or not name.startswith(bvid + '-'):
            raise p.Paused('正式索引里的路径不对（' + str(name) + '）。请检查 data/formal/index.json。')
        entry = data / 'formal' / name
        gate = entry / 'gate.json'
        if not gate.exists() or not p.load(gate).get('passed'):
            continue
        source = p.load(entry / 'source.json')
        library.append({'id': name, 'title': source.get('title') or bvid,
                        'url': source.get('canonical') or sources.Profile(bvid).url,
                        'source': source, 'notes': (entry / 'notes.md').read_text(encoding='utf-8')})
    # 新入库的排前面；没有 published_at 的按索引原有顺序。
    library.sort(key=lambda item: -(item['source'].get('published_at') or 0))

    pending_root = data / 'pending'
    queued, jobs = [], []
    for record in sorted(pending_root.glob('*/acquisition.json')) if pending_root.exists() else []:
        bvid = record.parent.name
        if bvid in index:
            continue  # 已经正式入库，不再算是待处理
        acquisition = p.load(record)
        queued.append(acquisition)
        for path in p.analysis_states(record.parent):
            state = p.load(path)
            records = [p.load(f) for f in path.parent.glob('calls/*/call.json')]
            jobs.append({'id': path.parent.name, 'source': bvid,
                         'title': acquisition.get('title') or sources.Profile(bvid).display_title,
                         'url': acquisition.get('canonical') or sources.Profile(bvid).url,
                         'status': state['status'], 'reason': state.get('reason'),
                         'frames': state.get('frames'),
                         'completed': sum(r.get('status') == 'complete' for r in records),
                         'batches': ((state.get('frames', 0) + p.BATCH - 1) // p.BATCH),
                         'attempts': len(records) + len(list(path.parent.glob('call-history/*/call.json')))})
    return {'library': library, 'queued': queued, 'jobs': jobs,
            'formal': library[0] if library else None,
            'acquisition': queued[0] if queued else {}}


class LocalServer(ThreadingHTTPServer):
    """Windows 上必须关掉地址复用，否则第二个进程能「抢」走已经占用的端口。"""
    allow_reuse_address = os.name != 'nt'
    daemon_threads = True


def make_server(data, host='127.0.0.1', port=8765, token=None, proxy=None):
    token = token or secrets.token_urlsafe(32)
    lock = threading.Lock()
    running = [None]
    from .semantic import LocalGraph
    graph = LocalGraph(data)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # 日志里不出现口令、来源内容和查询串。

        def respond(self, code, body, mime='application/json; charset=utf-8'):
            raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)

        def query_params(self):
            return parse_qs(urlsplit(self.path).query)

        def graph_params(self):
            params = self.query_params()

            def value(name, default):
                return params.get(name, [default])[0]

            try:
                return dict(query=value('q', ''), focus=value('focus', ''),
                            offset=int(value('offset', '0')), limit=int(value('limit', '24')),
                            threshold=float(value('threshold', '.65')), include=value('include', ''))
            except ValueError as error:
                raise p.Paused('知识图谱的参数看不懂（offset、limit、threshold 需要是数字）。刷新页面即可恢复。') from error

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/favicon.ico':
                return self.respond(204, b'', 'image/x-icon')
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path in assets:
                name, mime = assets[path]
                return self.respond(200, (Path(__file__).parent / 'static' / name).read_bytes(), mime)
            if not self.authorized():
                return self.respond(401, {'error': NO_TOKEN})
            try:
                if path == '/api/taxonomy':
                    from .taxonomy import view
                    return self.respond(200, view(data))
                if path == '/api/graph':
                    return self.respond(200, graph.graph(**self.graph_params()))
                if path == '/api/state':
                    return self.respond(200, snapshot(data))
                if path == '/api/query':
                    selected = self.query_params().get('entry', [None])[0]
                    library = snapshot(data)['library']
                    if selected:
                        match = next((f for f in library if f['id'] == selected), None)
                        if not match:
                            return self.respond(404, {'error': '没有这份资料。它可能还没通过发布检查，或者已经被替换成新版本。'})
                        return self.respond(200, {'text': match['notes']})
                    if library:
                        return self.respond(200, {'text': library[0]['notes']})
                    return self.respond(200, {'text': '还没有通过门禁的正式资料。待处理的材料不向查询开放。'})
                if path.startswith('/api/frame/'):
                    return self.send_frame(path.removeprefix('/api/frame/'))
                return self.respond(404, {'error': '没有这个地址。'})
            except p.Paused as error:
                return self.respond(400, {'error': str(error)})
            except (ValueError, TypeError, KeyError) as error:
                return self.respond(400, {'error': BAD_REQUEST + '（' + type(error).__name__ + '）'})
            except Exception:
                traceback.print_exc()
                return self.respond(500, {'error': UNEXPECTED})

        def send_frame(self, name):
            index = data / 'formal/index.json'
            if not index.exists() or not p.load(index):
                return self.respond(404, {'error': '还没有正式入库的资料，所以没有截图。'})
            names = p.load(index)
            entry_id = self.query_params().get('entry', [None])[0] or next(iter(names.values()))
            if entry_id not in names.values():
                return self.respond(404, {'error': '没有这份资料，截图取不到。'})
            entry = data / 'formal' / entry_id
            gate = entry / 'gate.json'
            if not gate.exists() or not p.load(gate).get('passed'):
                return self.respond(404, {'error': '这份资料还没通过发布检查，截图暂时不开放。'})
            allowed = {Path(f['file']).name for f in p.load(entry / 'frames.json')}
            file = (entry / 'frames' / name).resolve()
            if name not in allowed or file.parent != (entry / 'frames').resolve():
                return self.respond(404, {'error': '没有这张截图。'})
            return self.respond(200, file.read_bytes(), 'image/jpeg')

        def do_POST(self):
            if not self.authorized():
                return self.respond(401, {'error': NO_TOKEN})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    return self.respond(413, {'error': '这次请求是空的或太大了。刷新页面后重新提交。'})
                obj = json.loads(self.rfile.read(size))
                path = urlsplit(self.path).path
                if path == '/api/taxonomy':
                    from .taxonomy import apply
                    return self.respond(200, apply(data, obj))
                if path == '/api/submit':
                    return self.submit(obj)
                if path == '/api/analyze':
                    return self.analyze(obj)
                return self.respond(404, {'error': '没有这个地址。'})
            except json.JSONDecodeError:
                return self.respond(400, {'error': '请求内容不是合法的 JSON，刷新页面后重新提交。'})
            except (ValueError, TypeError, p.Paused) as error:
                return self.respond(400, {'error': str(error)})
            except Exception:
                traceback.print_exc()
                return self.respond(500, {'error': UNEXPECTED})

        def submit(self, obj):
            """接收一条链接：能复用就复用，不能复用就启动导入。"""
            info = p.identify(obj.get('url', ''), data)
            bvid = info['bvid']
            current = snapshot(data)
            if any(item['source'].get('bvid') == bvid for item in current['library']):
                return self.respond(200, {'message': '这条视频已经入库了，直接打开笔记就行，不会重复花额度。',
                                          'state': current})
            queued = next((item for item in current['queued'] if item.get('bvid') == bvid), None)
            jobs = [job for job in current['jobs'] if job['source'] == bvid]
            retry = bool(queued and 'paused' in queued.get('status', '')) or any(
                job['status'] == 'paused' for job in jobs)
            if (queued or jobs) and not retry:
                return self.respond(200, {'message': '这条视频已经在待处理里了，去那边看进度。', 'state': current})
            title = sources.profile(data, bvid).display_title
            if obj.get('allow_cloud') is not True:
                return self.respond(200, {'message': f'链接记下了（{title}）。要开始整理，'
                                                     '先展开「导入选项」勾选同意，再提交一次。',
                                          'state': current})
            if data.resolve() != p.DEFAULT_DATA.resolve():
                raise p.Paused('网页导入只在默认资料目录 data/ 下可用；换目录请用命令行。')
            with lock:
                if running[0] and running[0].poll() is None:
                    return self.respond(200, {'message': '已经在导入了，去「待处理」看进度。', 'state': current})
                args = [sys.executable, '-m', 'snakekb.ingest', '--source', bvid, '--allow-cloud-analysis']
                if retry:
                    args += ['--retry']
                args += ['--proxy', proxy] if proxy else ['--no-proxy']
                running[0] = subprocess.Popen(args, cwd=p.ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self.respond(202, {'message': f'已开始整理《{title}》：先取素材，再按素材形态选字幕、'
                                                 '语音转写或画面文字，最后交给 Codex 分析。'
                                                 '可以去「待处理」看进度，关掉页面也会继续。',
                                      'state': current})

        def analyze(self, obj):
            if obj.get('allow_cloud') is not True:
                raise p.Paused('要先勾选同意，才能把截图和画面文字交给 Codex 分析。')
            jobs = {j['id']: j for j in snapshot(data)['jobs']}
            name = obj.get('job')
            if name not in jobs:
                raise p.Paused('这个任务不在待处理列表里，刷新页面看看它是否已经完成。')
            job = data / 'pending' / jobs[name]['source'] / name
            with lock:
                if (running[0] and running[0].poll() is None) or jobs[name]['status'] == 'analyzing':
                    raise p.Paused('这个任务正在跑，等它结束再看结果。')
                args = [sys.executable, '-m', 'snakekb', 'run', '--data', str(data),
                        '--source', jobs[name]['source'], '--job', str(job), '--allow-cloud-analysis']
                args += ['--proxy', proxy] if proxy else ['--no-proxy']
                if jobs[name]['status'] == 'paused':
                    if obj.get('explicit_retry') is not True:
                        raise p.Paused('这是暂停过的任务。要重新开始，请先勾选「重新尝试」，旧记录和已用额度会保留。')
                    args += ['--retry-analysis']
                running[0] = subprocess.Popen(args, cwd=p.ROOT, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
            return self.respond(202, {'message': '已开始分析（不设本地调用次数上限）。可以关掉页面，处理会在后台继续。'})

    server = LocalServer((host, port), Handler)
    server.access_token = token
    return server


def bind(data, host, port, token=None, proxy=None, attempts=20):
    """端口被占用时自动往后找，避免「启动失败但不知道为什么」。"""
    last = None
    for candidate in range(port, port + attempts):
        try:
            return make_server(data, host, candidate, token, proxy), candidate
        except OSError as error:
            last = error
            if getattr(error, 'errno', None) not in {48, 98, 10048}:
                raise
    raise p.Paused(f'{port} 起的 {attempts} 个端口都被占用了。'
                   f'关掉之前的启动窗口，或用 --port 指定别的端口。最后一次错误：{last}')


def main():
    parser = argparse.ArgumentParser(description='在浏览器里看本地知识库')
    parser.add_argument('--host', default='127.0.0.1', choices=['127.0.0.1', '0.0.0.0'],
                        help='默认只允许本机访问；0.0.0.0 才会让同一局域网的手机连进来')
    parser.add_argument('--port', type=int, default=8765, help='端口被占用时会自动往后找')
    parser.add_argument('--data', type=Path, default=p.DEFAULT_DATA, help='资料目录，默认是仓库里的 data')
    parser.add_argument('--proxy', default=os.environ.get('SNAKEKB_PROXY', DEFAULT_PROXY),
                        help='只给模型子进程用的 HTTP(S) 代理，默认 http://127.0.0.1:65532')
    parser.add_argument('--no-proxy', action='store_true', help='模型调用不走代理')
    parser.add_argument('--open', action='store_true', help='启动后自动打开浏览器')
    args = parser.parse_args()

    proxy = None if args.no_proxy else (args.proxy or None)
    try:
        server, port = bind(args.data.resolve(), args.host, args.port, proxy=proxy)
    except OSError as error:
        print(f'启动失败：端口或地址不可用（{error}）。换个端口试试：--port 8766', file=sys.stderr)
        raise SystemExit(2)
    except p.Paused as error:
        print('启动失败：' + str(error), file=sys.stderr)
        raise SystemExit(2)

    url = f'http://127.0.0.1:{port}/#key={server.access_token}'
    print('私人知识库已启动。', flush=True)
    print('  本机打开：' + url, flush=True)
    print('  口令就在这行地址里 #key= 后面；每次启动都会换一个。', flush=True)
    if port != args.port:
        print(f'  端口 {args.port} 被占用，已改用 {port}。', flush=True)
    if proxy:
        print('  模型调用只对子进程走代理：' + proxy + '（不想走代理加 --no-proxy）', flush=True)
    else:
        print('  模型调用不走代理。', flush=True)
    if args.host == '0.0.0.0':
        print('  局域网访问：把上面地址里的 127.0.0.1 换成本机局域网 IP，口令保持不变。', flush=True)
        print('  只在家里的可信网络这样用，不要映射到公网。', flush=True)
    print('  关闭这个窗口就停止服务。', flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('已停止。', flush=True)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
