"""一次性下载本地向量模型；装好之后检索不再联网，日常使用也不下载任何东西。"""
import argparse
import hashlib
import urllib.request
from .semantic import MODEL_DIR, MODEL
from . import pipeline as p

REVISION = '75c43b069aac4d136ba6bc1122f995fedcfd2781'

def install(proxy=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': proxy} if proxy else {}))
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    files = {}
    for remote in ('tokenizer.json', 'config.json', 'onnx/model_quantized.onnx'):
        path = MODEL_DIR / remote.split('/')[-1]
        if not path.exists():
            url = f'https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/{REVISION}/{remote}'
            part = path.with_suffix('.part')
            with opener.open(url, timeout=60) as response, part.open('wb') as output:
                while block := response.read(1024*1024):
                    output.write(block)
            part.replace(path)
        files[path.name] = {'bytes': path.stat().st_size, 'sha256': p.digest(path)}
    p.write(MODEL_DIR / 'manifest.json', {'model': MODEL, 'files': files})
    return MODEL_DIR

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='下载约 25MB 的本地向量模型，装一次就够，之后检索完全离线。')
    parser.add_argument('--proxy',default=None,help='下载走代理时填写，例如 http://127.0.0.1:65532')
    location = install(parser.parse_args().proxy)
    print('向量模型已就位：' + str(location))
    print('现在可以正常启动知识库，检索和知识图谱都用它。')
