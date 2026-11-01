"""Local evolving taxonomy. Suggestions never mutate the taxonomy without approval."""
import copy
import hashlib
from pathlib import Path
import threading
from . import pipeline as p
from .domains import DOMAINS

LOCK = threading.RLock()
WORDS = {'algorithms':['算法','贪吃蛇','数据结构'], 'ai':['deepseek','qwen','glm','模型','qoder'],
         'agents':['agent','智能体','harness'], 'society':['网友','政治','社会','混战']}
COLORS = ['#268078','#4778c5','#8959b5','#c28132','#77838d']

def read(data):
    path=Path(data)/'taxonomy.json'
    if path.exists(): return p.load(path)
    return {'revision':0,'domains':{k:{'label':v,'color':COLORS[i],'keywords':WORDS.get(k,[])} for i,(k,v) in enumerate(DOMAINS.items())},'history':[]}

def materials(data):
    index=Path(data)/'formal/index.json'
    result=[]
    for key,name in (p.load(index) if index.exists() else {}).items():
        if Path(name).name!=name or not name.startswith(key+'-'): raise p.Paused('资料索引路径不合法')
        entry=Path(data)/'formal'/name
        if not p.load(entry/'gate.json').get('passed'): continue
        source=p.load(entry/'source.json')
        from .concepts import read_artifact
        artifact=read_artifact(entry) or {}
        concepts=[c['name'] for c in artifact.get('concepts',[])]
        text=' '.join([source.get('title',''),*concepts,*[c['definition'] for c in artifact.get('concepts',[])]])
        if not artifact: text+=' '+(entry/'notes.md').read_text(encoding='utf8')
        result.append({'id':name,'title':source.get('title',key),'text':text.casefold(),'concepts':concepts})
    return result

def classify(item, state):
    hits=[k for k,v in state['domains'].items() if k!='other' and any(w.casefold() in item['text'] for w in v['keywords'])]
    for k in list(hits):
        parent=state['domains'][k].get('parent')
        seen=set(hits)
        while parent and parent not in seen:
            hits.append(parent);seen.add(parent)
            parent=state['domains'].get(parent,{}).get('parent')
    return hits or ['other']

def view(data, state=None):
    state=read(data) if state is None else state
    items=materials(data)
    for item in items: item['categories']=classify(item,state)
    suggestions=[]
    members={k:{x['id'] for x in items if k in x['categories']} for k in state['domains'] if k!='other'}
    keys=list(members)
    for i,a in enumerate(keys):
        for b in keys[i+1:]:
            union=members[a]|members[b]
            if len(union)>=3 and len(members[a]&members[b])/len(union)>=.8:
                suggestions.append({'kind':'merge','ids':[a,b],'label':state['domains'][a]['label']+' / '+state['domains'][b]['label'],
                    'reason':'至少 3 份资料，领域归属重合率达到 80%；仅为候选，需检查主题是否等价。'})
    terms={}
    for item in items:
        for term in set(item['concepts']):
            if 2<=len(term)<=40: terms.setdefault(term,[]).append(item)
    known={w.casefold() for d in state['domains'].values() for w in d['keywords']}
    for term,group in sorted(terms.items(),key=lambda kv:(-len(kv[1]),kv[0])):
        if len(group)<2 or term.casefold() in known: continue
        unknown=[x for x in group if x['categories']==['other']]
        if len(unknown)>=2:
            suggestions.append({'kind':'create','label':term,'keywords':[term],'reason':'至少两份未分类资料共享此概念。'})
        else:
            parent=next((k for k,ids in members.items() if len(ids)>=4 and 2<=len({x['id'] for x in group}&ids)<len(ids)),None)
            if parent:
                suggestions.append({'kind':'split','parent':parent,'label':term,'keywords':[term], 'reason':'较大领域中至少两份资料共享此主题；建议建立子领域，其余资料保留父领域。'})
        if len(suggestions)>=20: break
    return {'revision':state['revision'],'domains':state['domains'],'canUndo':bool(state['history']),
            'sources':[{k:v for k,v in x.items() if k!='text'} for x in items], 'suggestions':suggestions}

def apply(data, request):
    with LOCK:
        state=read(data)
        if request.get('revision')!=state['revision']: raise p.Paused('分类已更新，请刷新预览后重试。')
        action=request.get('action'); before=copy.deepcopy(state['domains'])
        if action=='undo':
            if not state['history']: raise p.Paused('没有可撤销的分类变更。')
            state['domains']=state['history'].pop()['before']
        else:
            domains=state['domains']; label=str(request.get('label','')).strip()
            if not 1<=len(label)<=60: raise p.Paused('领域名称需要 1–60 个字符。')
            if action in {'create','split'}:
                words=request.get('keywords',[])
                if not isinstance(words,list) or not words or any(not isinstance(w,str) or not 2<=len(w.strip())<=60 for w in words): raise p.Paused('请填写 2–60 字符的分类关键词。')
                if action=='split' and request.get('parent') not in domains: raise p.Paused('父领域不存在。')
                key='domain-'+hashlib.sha256(label.encode()).hexdigest()[:12]
                if key in domains or any(v['label']==label for v in domains.values()): raise p.Paused('领域已经存在。')
                hue=int(hashlib.sha256(key.encode()).hexdigest()[:6],16)%360
                domains[key]={'label':label,'keywords':list(dict.fromkeys(w.strip() for w in words)), 'color':f'hsl({hue} 48% 43%)'}
                if action=='split': domains[key]['parent']=request['parent']
            elif action=='merge':
                ids=request.get('ids',[])
                if len(set(ids))<2 or any(k not in domains or k=='other' for k in ids): raise p.Paused('请选择至少两个有效领域。')
                keep=ids[0]; domains[keep]['label']=label
                domains[keep]['keywords']=sorted(set(w for k in ids for w in domains[k]['keywords']))
                for k in ids[1:]: del domains[k]
                for k,v in domains.items():
                    if v.get('parent') in ids: v.pop('parent') if k==keep else v.update(parent=keep)
            else: raise p.Paused('未知分类操作。')
            state['history'].append({'before':before,'action':action,'label':label,'revision':state['revision']})
        state.setdefault('audit',[]).append({'action':action,'before':before,'after':copy.deepcopy(state['domains']),'revision':state['revision']+1})
        state['revision']+=1
        if not request.get('preview'):
            p.write(Path(data)/'taxonomy.json',state)
        return view(data,state)
