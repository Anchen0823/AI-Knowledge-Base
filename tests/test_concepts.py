import copy
import unittest
from snakekb import pipeline as p
from snakekb.concepts import validate, canonical_id, claims_from_notes

class ConceptValidationTests(unittest.TestCase):
    def setUp(self):
        self.claims=[{'id':'c0000'},{'id':'c0001'}]
        self.answer={'uncertainties':[],'concepts':[
            {'name':'尾部可达性','type':'概念','definition':'检查是否可到达尾部','aliases':['尾巴可达'],
             'reuse_id':'','merge_reason':'同一检查的两种称谓','claim_ids':['c0000']},
            {'name':'谨慎策略','type':'算法','definition':'先模拟再判断','aliases':[],
             'reuse_id':'','merge_reason':'','claim_ids':['c0000','c0001']}],
            'relations':[{'source':'谨慎策略','target':'尾部可达性','label':'检查','explanation':'模拟进食后检查是否仍可到达尾部',
                          'basis':'原文关系','claim_ids':['c0000']}]}

    def test_complete_grounded_concepts(self):
        validate(self.answer,self.claims,[])

    def test_uncovered_claim_and_invalid_relation_rejected(self):
        bad=copy.deepcopy(self.answer);bad['concepts'][1]['claim_ids']=['c0000']
        with self.assertRaises(p.Paused):validate(bad,self.claims,[])
        bad=copy.deepcopy(self.answer);bad['relations'][0]['claim_ids']=['c0001']
        with self.assertRaises(p.Paused):validate(bad,self.claims,[])

    def test_merge_must_refer_to_real_canonical_entry_and_explain(self):
        concept=self.answer['concepts'][0]
        concept['reuse_id']=canonical_id(concept['name'],concept['type'])
        with self.assertRaises(p.Paused):validate(self.answer,self.claims,[])
        catalog=[dict(concept,id=concept['reuse_id'])]
        validate(self.answer,self.claims,catalog)
        concept['merge_reason']=''
        with self.assertRaises(p.Paused):validate(self.answer,self.claims,catalog)

    def test_claims_keep_conditions_and_provenance(self):
        text='- **原作者陈述**：一个结论\n  条件：偶数棋盘\n  出处：f0001\n- **来源局限**：未独立验证\n'
        claims=claims_from_notes(text)
        self.assertEqual(len(claims),1)
        self.assertIn('偶数棋盘',claims[0]['details'])
        self.assertIn('f0001',claims[0]['details'])

class ConceptPublicationTests(unittest.TestCase):
    def test_failed_concept_pass_cannot_publish_or_replace_index(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=p.ROOT/'validation/offline-test-tmp') as tmp:
            data=Path(tmp);job=data/'pending'/p.BVID/'new'
            p.write(job/'state.json',{'revision':'new'})
            p.write(data/'formal/index.json',{p.BVID:'original'})
            with patch.object(p,'final_gate',return_value=[]), patch.object(p,'notes',return_value='notes'), patch('snakekb.concepts.generate',side_effect=p.Paused('invalid concepts')):
                with self.assertRaises(p.Paused):p.publish(data,job)
            self.assertEqual(p.load(data/'formal/index.json'),{p.BVID:'original'})
            self.assertFalse((data/'formal'/(p.BVID+'-new')).exists())
