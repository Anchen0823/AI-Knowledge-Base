import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from snakekb import pipeline as p
from snakekb.taxonomy import read, view, apply, classify

class TaxonomyTests(unittest.TestCase):
    def test_multilabel_and_unknown(self):
        state=read(Path('nonexistent-taxonomy-test'))
        self.assertEqual(classify({'text':'unknown'},state),['other'])
        self.assertEqual(set(classify({'text':'agent deepseek'},state)),{'ai','agents'})

    def test_preview_merge_undo_stale_revision(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'validation/offline-test-tmp') as tmp:
            data=Path(tmp)
            request={'action':'merge','ids':['ai','agents'],'label':'人工智能','revision':0}
            with patch('snakekb.taxonomy.materials',return_value=[]):
                preview=apply(data,dict(request,preview=True))
                self.assertNotIn('agents',preview['domains'])
                self.assertFalse((data/'taxonomy.json').exists())
                result=apply(data,request)
                self.assertEqual(result['domains']['ai']['color'],'#4778c5')
                with self.assertRaises(p.Paused): apply(data,request)
                result=apply(data,{'action':'undo','revision':1})
                self.assertIn('agents',result['domains'])
                self.assertEqual(result['revision'],2)

    def test_split_dynamic_assignment(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'validation/offline-test-tmp') as tmp:
            with patch('snakekb.taxonomy.materials',return_value=[]):
                apply(Path(tmp),{'action':'split','parent':'ai','label':'视觉模型','keywords':['视觉'],'revision':0})
            state=read(Path(tmp)); ids=classify({'text':'视觉'},state)
            self.assertIn('ai',ids)
            self.assertEqual(len(ids),2)

    def test_new_domain_suggestion_requires_shared_evidence(self):
        samples=[{'id':str(i),'title':'星空','text':'天文观测','concepts':['天文观测']} for i in range(2)]
        with tempfile.TemporaryDirectory(dir=p.ROOT/'validation/offline-test-tmp') as tmp:
            with patch('snakekb.taxonomy.materials',return_value=samples):
                result=view(Path(tmp))
                self.assertTrue(any(s['kind']=='create' for s in result['suggestions']))
