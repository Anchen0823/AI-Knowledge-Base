import tempfile
import unittest
from pathlib import Path
from snakekb import pipeline as p
from snakekb.semantic import LocalGraph
from snakekb.concepts import canonical_id
import hashlib

class GraphTests(unittest.TestCase):
    def test_paging_links_and_removal_respect_formal_gate(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'validation/offline-test-tmp') as tmp:
            data=Path(tmp)
            index={}
            for i in range(2):
                name=f'BVtest{i}-revision'; index[f'BVtest{i}']=name
                folder=data/'formal'/name
                p.write(folder/'gate.json',{'passed':True})
                p.write(folder/'source.json',{'title':f'Source {i}'})
                (folder/'notes.md').write_text(f'- **原作者陈述**：共享知识点 {i}\n  条件：仅为测试\n  出处：s0001\n',encoding='utf-8')
                text=(folder/'notes.md').read_text(encoding='utf-8')
                cid=canonical_id(f'概念{i}','主题')
                p.write(folder/'concepts.json',{'version':1,'validated':True,'notes_sha256':hashlib.sha256(text.encode()).hexdigest(),
                    'claims':[{'id':'c0000','text':f'共享知识点 {i}','details':'出处：s0001','kind':'原作者陈述'}],
                    'concepts':[{'id':cid,'name':f'概念{i}','type':'主题','definition':f'定义{i}',
                        'aliases':[],'claim_ids':['c0000'],'merge_reason':''}],'relations':[]})
            p.write(data/'formal/index.json',index)
            graph=LocalGraph(data)
            graph.encode=lambda texts,query=False:[[1.,0.] for t in texts]
            first=graph.graph(limit=1)
            self.assertTrue(first['hasMore'])
            point=next(n for n in first['nodes'] if n['kind']=='concept')
            second=graph.graph(offset=1,limit=1,include=point['id'])
            self.assertFalse(second['hasMore'])
            self.assertTrue(any(e.get('crossSource') for e in second['edges']))
            graph.encode=lambda *a,**k: self.fail('cached document vectors were recomputed')
            self.assertEqual(graph.graph()['total'],2)
            p.write(data/'formal'/'BVtest1-revision'/'gate.json',{'passed':False})
            self.assertEqual(graph.graph()['total'],1)
            self.assertFalse(any('BVtest1' in n['entryId'] for n in graph.graph()['nodes']))
            with self.assertRaises(p.Paused):graph.graph(focus='missing')

    def test_shared_concept_preserves_both_sources_and_reindexes_changed_definition(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'validation/offline-test-tmp') as tmp:
            data=Path(tmp); index={}; identity=canonical_id('共同概念','概念')
            for i in range(2):
                name=f'BVshared{i}-revision';index[f'BVshared{i}']=name;entry=data/'formal'/name
                p.write(entry/'gate.json',{'passed':True});p.write(entry/'source.json',{'title':str(i)})
                (entry/'notes.md').write_text('notes',encoding='utf-8')
                p.write(entry/'concepts.json',{'version':1,'validated':True,'notes_sha256':hashlib.sha256(b'notes').hexdigest(),
                    'claims':[{'id':'c0000','text':f'论述{i}','details':f'条件{i}','kind':'原作者陈述'}],
                    'concepts':[{'id':identity,'name':'共同概念','type':'概念','definition':f'定义{i}',
                        'aliases':[f'别名{i}'],'claim_ids':['c0000'],'merge_reason':'同一个概念'}],'relations':[]})
            p.write(data/'formal/index.json',index)
            graph=LocalGraph(data); calls=[]
            def encode(texts,query=False):
                calls.extend(texts);return [[1.,0.] for _ in texts]
            graph.encode=encode
            result=graph.graph();points=[n for n in result['nodes'] if n['kind']=='concept']
            self.assertEqual(len(points),1)
            self.assertEqual(len(points[0]['evidence']),2)
            self.assertIn('定义0',points[0]['excerpt'])
            self.assertIn('定义1',points[0]['excerpt'])
            self.assertEqual(len(points[0]['contributions']),2)
            self.assertEqual(len(points[0]['sourceIds']),2)
            self.assertEqual(len(calls),1)
            hit=next(n for n in graph.graph(query='别名1')['nodes'] if n['kind']=='concept')
            self.assertGreater(hit['score'],2)
            self.assertEqual(len(calls),2)
            index.pop('BVshared1');p.write(data/'formal/index.json',index)
            result=graph.graph();point=next(n for n in result['nodes'] if n['kind']=='concept')
            self.assertEqual(len(point['evidence']),1)
            self.assertEqual(len(calls),3)


class SynthesisTests(unittest.TestCase):
    def test_synthesis_invalidated_when_source_removed(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=p.ROOT/'validation/offline-test-tmp') as tmp:
            data=Path(tmp); index={'a':'a-r','b':'b-r'}
            inputs={'a-r':'hash','b-r':'hash'}
            for name in index.values():
                p.write(data/'formal'/name/'gate.json',{'passed':True})
                p.write(data/'formal'/name/'source.json',{'title':name})
            p.write(data/'formal/index.json',index)
            artifact={'notes_sha256':'hash','claims':[], 'relations':[], 'concepts':[
                {'id':'same','name':'概念','type':'概念','definition':'来源定义','aliases':[],
                 'claim_ids':[],'merge_reason':'', 'synthesis':{'summary':'综合解释',
                 'inputs':inputs,'conditions':['条件'],'differences':['差异'],'updated_at':1}}]}
            with patch('snakekb.concepts.read_artifact',return_value=artifact):
                node=LocalGraph(data).documents()[1][0]
                self.assertEqual(node['excerpt'],'综合解释')
                self.assertEqual(node['conditions'],['条件'])
                p.write(data/'formal/index.json',{'a':'a-r'})
                node=LocalGraph(data).documents()[1][0]
                self.assertEqual(node['excerpt'],'来源定义')
                self.assertEqual(node['differences'],[])
