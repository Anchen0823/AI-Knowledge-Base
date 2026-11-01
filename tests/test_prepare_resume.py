import tempfile
import unittest
from pathlib import Path
from snakekb import pipeline as p

class ResumeTests(unittest.TestCase):
    def test_retry_before_first_model_call(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'validation/offline-test-tmp') as tmp:
            job=Path(tmp)
            p.write(job/'state.json',{'status':'paused'})
            p.reopen_analysis(job)
            self.assertEqual(p.load(job/'state.json')['status'],'prepared')
            self.assertTrue((job/'call-history').exists())

    def test_gaps_accepts_mixed_intervals_with_equal_start(self):
        self.assertEqual(p.gaps([(0, 2), [0, 1], [3, 4]], 4), [[2, 3]])
