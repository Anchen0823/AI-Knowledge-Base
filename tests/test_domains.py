import unittest
from snakekb.domains import category

class DomainTests(unittest.TestCase):
    def test_categories(self):
        for title, expected in [('贪吃蛇策略','algorithms'),('Qwen4 发布','ai'),('Super-agent 系统','agents'),('二十国混战：网友对线','society'),('无标题','other')]:
            self.assertEqual(category({'title':title}),expected)
        self.assertEqual(category({'title':'Agent','category':'society'}),'society')
