"""本地向量检索与跨资料知识图谱。

向量模型在本地 CPU 上跑，索引和查询都不联网；只有入库时整理概念会用到 Codex。
相似度只用来找候选，不代表因果、支持或等价关系。
"""
import hashlib
import re
import threading
from pathlib import Path

from . import pipeline as p
from .domains import category, DOMAINS

MODEL = 'Xenova/bge-small-zh-v1.5@75c43b069aac4d136ba6bc1122f995fedcfd2781/int8'
MODEL_DIR = p.ROOT / '.models/bge-small-zh-v1.5'


class LocalGraph:
    def __init__(self, data, model_dir=MODEL_DIR):
        self.data, self.model_dir = Path(data), Path(model_dir)
        self.lock = threading.RLock()
        self.session = self.tokenizer = None
        self.timer = None

    def _unload(self):
        with self.lock:
            self.session = self.tokenizer = None

    def encode(self, texts, query=False):
        import numpy as np
        with self.lock:
            if self.session is None:
                import onnxruntime as ort
                from tokenizers import Tokenizer
                if not (self.model_dir / 'model_quantized.onnx').exists():
                    raise p.Paused('本地向量模型还没装。运行 python -m snakekb.install_vectors --proxy http://127.0.0.1:65532 装一次，之后日常使用不需要联网。')
                options = ort.SessionOptions()
                options.intra_op_num_threads = 4
                self.session = ort.InferenceSession(str(self.model_dir / 'model_quantized.onnx'), options, providers=['CPUExecutionProvider'])
                self.tokenizer = Tokenizer.from_file(str(self.model_dir / 'tokenizer.json'))
                self.tokenizer.enable_truncation(max_length=512)
                self.tokenizer.enable_padding()
            result = []
            for offset in range(0, len(texts), 16):
                batch = texts[offset:offset+16]
                if query:
                    batch = ['为这个句子生成表示以用于检索相关文章：' + t for t in batch]
                tokens = self.tokenizer.encode_batch(batch)
                values = {'input_ids': np.array([t.ids for t in tokens], dtype=np.int64),
                          'attention_mask': np.array([t.attention_mask for t in tokens], dtype=np.int64),
                          'token_type_ids': np.array([t.type_ids for t in tokens], dtype=np.int64)}
                output = self.session.run(None, {i.name: values[i.name] for i in self.session.get_inputs()})[0]
                vector = output[:, 0, :] if output.ndim == 3 else output
                vector /= np.maximum(np.linalg.norm(vector, axis=1, keepdims=True), 1e-12)
                result.extend(vector.tolist())
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(300, self._unload)
            self.timer.daemon = True
            self.timer.start()
            return result

    def documents(self):
        from .concepts import read_artifact
        index_file = self.data / 'formal/index.json'
        if not index_file.exists():
            self.relations = []
            return [], []
        sources, merged, relations = [], {}, []
        for bvid, name in sorted(p.load(index_file).items()):
            if Path(name).name != name or not name.startswith(bvid + '-'):
                raise p.Paused('正式索引里的路径不对（' + str(name) + '）。请检查 data/formal/index.json。')
            entry = self.data / 'formal' / name
            if not p.load(entry / 'gate.json').get('passed'):
                continue
            source = p.load(entry / 'source.json')
            domain = category(source)
            sources.append({'id': 'source:' + name, 'entryId': name, 'label': source['title'],
                            'kind': 'source', 'excerpt': source['title'], 'categories':[domain], 'categoryLabels':[DOMAINS[domain]]})
            artifact = read_artifact(entry)
            if not artifact:
                continue
            claims = {c['id']: c for c in artifact['claims']}
            for concept in artifact['concepts']:
                evidence = [dict(claims[cid], entryId=name, sourceTitle=source['title']) for cid in concept['claim_ids']]
                identity = concept['id']
                if identity not in merged:
                    merged[identity] = {'id': identity, 'entryId': name, 'sourceId': 'source:' + name,
                        'sourceIds': [], 'label': concept['name'], 'kind': 'concept',
                        'excerpt': concept['definition'], 'definitions': [], 'aliases': [],
                        'details': '', 'claimType': concept['type'], 'sourceTitle': source['title'],
                        'evidence': [], 'mergeReasons': [], 'limitations': [],
                        'contributions': [], 'syntheses': [], 'categories': []}
                node = merged[identity]
                node['categories'] = sorted(set(node['categories'] + [domain]))
                node['categoryLabels'] = [DOMAINS[c] for c in node['categories']]
                node['sourceIds'].append('source:' + name)
                node['contributions'].append({'entryId':name,'sourceTitle':source['title'],
                    'definition':concept['definition'],'notes_sha256':artifact['notes_sha256'],
                    'publishedAt':source.get('published_at',0)})
                if concept.get('synthesis'):
                    node['syntheses'].append(concept['synthesis'])
                node['definitions'].append(concept['definition'])
                node['aliases'] = sorted(set(node['aliases'] + concept['aliases']))
                node['evidence'].extend(evidence)
                node['limitations'] = sorted(set(node['limitations'] + artifact.get('source_limitations', [])))
                if concept['merge_reason']:
                    node['mergeReasons'].append(concept['merge_reason'])
                node['details'] = ' · '.join(node['aliases'])
            for relation in artifact['relations']:
                relations.append(dict(relation, kind=relation['label'], entryId=name,
                    sourceTitle=source['title'], evidence=[dict(claims[cid], entryId=name, sourceTitle=source['title']) for cid in relation['claim_ids']]))
        self.relations = relations
        nodes = list(merged.values())
        for node in nodes:
            inputs={c['entryId']:c['notes_sha256'] for c in node['contributions']}
            valid=[s for s in node.pop('syntheses') if s.get('inputs')==inputs]
            node['contributions'].sort(key=lambda c:(-c['publishedAt'],c['entryId']))
            node['differences']=[]; node['conditions']=[]
            node['synthesisStatus']='source_summary'
            node['excerpt']='\n\n'.join(dict.fromkeys(c['definition'] for c in node['contributions']))
            if valid:
                latest=max(valid,key=lambda s:s.get('updated_at',0))
                node['excerpt']=latest['summary']
                node['differences']=latest.get('differences',[])
                node['conditions']=latest.get('conditions',[])
                node['synthesisStatus']='synthesized'
            node['embeddingText'] = node['label'] + ' ' + ' '.join(node['aliases']) + '\n' + '\n'.join(node['definitions']) + '\n' + node['excerpt']
            node['embeddingKey'] = hashlib.sha256(node['embeddingText'].encode()).hexdigest()
        return sources, nodes

    def graph(self, query='', focus='', offset=0, limit=24, threshold=.65, include=''):
        import numpy as np
        with self.lock:
            sources, nodes = self.documents()
            limit = min(80, max(1, limit)); offset = max(0, offset)
            threshold = max(.4, min(.95, threshold))
            if not nodes:
                return {'nodes': sources, 'edges': [], 'total': 0, 'hasMore': False, 'model': MODEL}
            cache_path = self.data / 'vectors/bge-v1.json'
            cache = p.load(cache_path) if cache_path.exists() else {}
            if cache.get('model') != MODEL:
                cache = {'model': MODEL, 'vectors': {}}
            vectors = cache['vectors']
            missing = [n for n in nodes if n['embeddingKey'] not in vectors]
            if missing:
                encoded = self.encode([n['embeddingText'] for n in missing])
                vectors.update({n['embeddingKey']: v for n, v in zip(missing, encoded)})
                p.write(cache_path, cache)
            matrix = np.array([vectors[n['embeddingKey']] for n in nodes], dtype=np.float32)
            scores = None
            if query.strip():
                if len(query) > 500:
                    raise p.Paused('搜索词最多 500 字。写短一点、只留关键词更容易搜到。')
                scores = matrix @ np.array(self.encode([query.strip()], query=True)[0])
                normalized_query = query.strip().casefold()
                for i, node in enumerate(nodes):
                    names = [node['label'], *node['aliases']]
                    if any(normalized_query == name.casefold() for name in names):
                        scores[i] += 2
                    elif len(normalized_query) > 1 and any(normalized_query in name.casefold() for name in names):
                        scores[i] += .3
            elif focus:
                target = next((i for i, n in enumerate(nodes) if n['id'] == focus), None)
                if target is None:
                    raise p.Paused('没有这个知识点。它可能属于别的资料，或已随资料更新被替换。')
                scores = matrix @ matrix[target]
                neighbours = {r['target'] if r['source'] == focus else r['source'] for r in self.relations if focus in (r['source'], r['target'])}
                for i, node in enumerate(nodes):
                    if node['id'] in neighbours or i == target:
                        scores[i] += 2
            if scores is not None:
                order = np.argsort(-scores).tolist()
            else:
                # Interleave sources so the first page already includes different materials.
                groups = [[i for i,n in enumerate(nodes) if n['sourceId']==s['id']] for s in sources]
                order = [g[i] for i in range(max(map(len,groups), default=0)) for g in groups if i<len(g)]
            selected = order[offset:offset+limit]
            visible = [dict(nodes[i], score=round(float(scores[i]), 4) if scores is not None else None) for i in selected]
            edges = [{'source': sid, 'target': n['id'], 'kind': '来源'} for n in visible for sid in n['sourceIds']]
            # Return sparse strongest neighbours; similarity is not logical entailment.
            prior = set(include.split(','))
            selected_set = set(selected) | {i for i,n in enumerate(nodes) if n['id'] in prior}
            visible_ids = {nodes[i]['id'] for i in selected_set}
            explicit = [r for r in self.relations if r['source'] in visible_ids and r['target'] in visible_ids]
            edges.extend(explicit)
            explicit_pairs = {frozenset((r['source'], r['target'])) for r in explicit}
            pairs = set()
            for i in sorted(selected_set):
                similarity = matrix @ matrix[i]
                candidates = [j for j in np.argsort(-similarity) if j != i and j in selected_set and similarity[j]>=threshold][:3]
                for j in candidates:
                    pair = tuple(sorted((i,int(j))))
                    if pair not in pairs and frozenset((nodes[i]['id'], nodes[j]['id'])) not in explicit_pairs:
                        pairs.add(pair)
                        edges.append({'source': nodes[i]['id'], 'target': nodes[j]['id'],
                                      'kind': '语义相近', 'score': round(float(similarity[j]),4),
                                      'crossSource': nodes[i]['entryId'] != nodes[j]['entryId']})
            return {'nodes': sources + visible, 'edges': edges, 'total': len(nodes),
                    'offset': offset, 'hasMore': offset + limit < len(nodes), 'model': MODEL,
                    'threshold': threshold, 'localOnly': True, 'conceptGenerator': 'Codex at ingestion'}
