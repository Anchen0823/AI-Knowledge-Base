"""来源登记：一条链接该按什么规矩处理。

内置两条人工核实过的样例；其余链接第一次成功下载后会把标题和时长记进
data/sources.json，之后就跟样例一样有确定的身份，不再每次重新猜。

这里只做离线判断，不联网。短链（b23.tv）的展开放在 acquire 里，因为那需要请求。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .errors import Paused

BVID_PATTERN = re.compile(r'BV[1-9A-HJ-NP-Za-km-z]{10}')
VIDEO_PATH = re.compile(r'/(?:zh-Hans/)?video/(BV[1-9A-HJ-NP-Za-km-z]{10})/?')
HOSTS = {'bilibili.com', 'www.bilibili.com', 'm.bilibili.com'}
# 已经核实过的短链对应关系，写在代码里就不用再发一次重定向请求。
SHORT_LINKS = {'Zemg4oc': 'BV1QTHA6DELc'}
REGISTRY = 'sources.json'

# media 的三种取值：
#   captions    —— 平台带字幕，直接按字幕核对
#   screen_text —— 只有背景音乐、讲解在画面上，走画面文字
#   auto        —— 下完再看：有平台字幕用字幕；有人声走本地转写；没人声走画面文字
MEDIA = {'captions', 'screen_text', 'auto'}


@dataclass(frozen=True)
class Profile:
    """一条来源的处理规矩。"""

    bvid: str
    title: str = ''
    duration: float | None = None  # None 表示不拿固定时长卡素材，改用下载时记录的真实时长
    media: str = 'auto'
    topics: tuple = ()  # 发布前必须看到明确出处的条件主题；空表示不限定
    verified: bool = False  # 当初人工逐条核对过的样例
    note: str = ''

    @property
    def url(self):
        if self.bvid.startswith('opus'):
            return f'https://www.bilibili.com/opus/{self.bvid[4:]}'
        return f'https://www.bilibili.com/video/{self.bvid}/'

    @property
    def display_title(self):
        return self.title or self.bvid

    def as_dict(self):
        return {'bvid': self.bvid, 'title': self.title, 'duration': self.duration,
                'media': self.media, 'topics': list(self.topics),
                'verified': self.verified, 'note': self.note}


BUILTIN = {
    'BV1QTHA6DELc': Profile(
        bvid='BV1QTHA6DELc',
        title='吃满最快的贪吃蛇，恰恰是最小心的那条｜退路',
        duration=485.0,
        media='captions',
        topics=('grid_parity', 'edge_wrapping', 'performance_scope'),
        verified=True,
        note='贪吃蛇策略样例：发布前必须看到棋盘奇偶性、穿边条件、评测范围的明确出处。',
    ),
    'BV1kind6mER6': Profile(
        bvid='BV1kind6mER6',
        title='Qoder为Deepseek V4.1 Flash扩容并增加TPM，减少用户排队 | 10月26日AI日报第560期',
        duration=118.65,
        media='screen_text',
        verified=True,
        note='AI 日报样例：只有背景音乐，讲解全在画面文字上。',
    ),
}


def parse(value):
    """把用户粘进来的东西拆成 {kind, bvid, url}，看不懂就说明该怎么改。"""
    value = (value or '').strip()
    if not value:
        raise Paused('还没有填链接。粘贴一条 B 站视频地址，或直接贴 BV 号。')
    if re.fullmatch(r'opus[0-9]+', value):
        return {'kind': 'opus', 'bvid': value, 'url': Profile(value).url}
    if BVID_PATTERN.fullmatch(value):
        return {'kind': 'bvid', 'bvid': value, 'url': Profile(value).url}
    parts = urlsplit(value)
    if parts.scheme not in {'http', 'https'}:
        raise Paused('这不像一条链接：需要 http(s):// 开头，或者直接贴 BV 号（形如 BV1QTHA6DELc）。')
    if parts.username or parts.password or parts.port:
        raise Paused('链接里带了用户名、密码或端口，这类地址不接受。请用浏览器地址栏里的原始地址。')
    host = (parts.hostname or '').lower()
    if host == 'b23.tv':
        known = SHORT_LINKS.get(parts.path.strip('/'))
        if known:
            return {'kind': 'bvid', 'bvid': known, 'url': Profile(known).url}
        return {'kind': 'short', 'bvid': None, 'url': value}
    if host in HOSTS:
        opus = re.fullmatch(r'/opus/([0-9]+)/?', parts.path)
        if opus:
            key = 'opus' + opus[1]
            return {'kind': 'opus', 'bvid': key, 'url': Profile(key).url}
        match = VIDEO_PATH.fullmatch(parts.path)
        if not match:
            raise Paused('这个地址不是单条视频页。需要形如 https://www.bilibili.com/video/BV…/ 的地址。')
        if parse_qs(parts.query).get('p', ['1']) != ['1']:
            raise Paused('链接带了第 2 个及以后的分 P 参数。这里只处理第 1 P：去掉分 P 参数，或改成 p=1 再试。')
        return {'kind': 'bvid', 'bvid': match.group(1), 'url': Profile(match.group(1)).url}
    raise Paused('只接受 bilibili.com 的视频地址、b23.tv 短链，或 BV 号。'
                 '其他站点的链接这里不会去请求。')


def read_registry(data):
    """读 data/sources.json；格式不对就说清楚哪一条有问题。"""
    path = Path(data) / REGISTRY
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as error:
        raise Paused(f'{path} 不是合法的 JSON（第 {error.lineno} 行）。'
                     '可以整份删掉，已入库的资料不会受影响，只是这些来源要重新登记一次。') from None
    if not isinstance(raw, dict):
        raise Paused(f'{path} 的内容应该是一个对象：键是 BV 号，值是标题、时长等信息。')
    return raw


def write_registry(data, entries):
    path = Path(data) / REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
    temp.replace(path)


def profile(data, bvid):
    """查一条来源的规矩。没登记过就返回通用规矩：标题和时长等下载后再说。"""
    if bvid in BUILTIN:
        return BUILTIN[bvid]
    entry = read_registry(data).get(bvid) or {}
    if not isinstance(entry, dict):
        raise Paused(f'{Path(data) / REGISTRY} 里 {bvid} 那一条应该是对象，现在不是。')
    duration = entry.get('duration')
    media = entry.get('media', 'auto')
    return Profile(
        bvid=bvid,
        title=str(entry.get('title') or ''),
        duration=float(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
        media=media if media in MEDIA else 'auto',
        topics=tuple(entry.get('topics') or ()),
        verified=bool(entry.get('verified')),
        note=str(entry.get('note') or ''),
    )


def remember(data, bvid, title=None, duration=None, media=None, note=None):
    """第一次成功拿到素材后登记这条来源。内置样例不动。"""
    if bvid in BUILTIN:
        return BUILTIN[bvid]
    entries = read_registry(data)
    entry = dict(entries.get(bvid) or {})
    if title:
        entry['title'] = str(title)
    if isinstance(duration, (int, float)) and duration > 0:
        entry['duration'] = round(float(duration), 3)
    if media in MEDIA:
        entry['media'] = media
    if note:
        entry['note'] = str(note)
    entries[bvid] = entry
    write_registry(data, entries)
    return profile(data, bvid)
