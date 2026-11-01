import json
import tempfile
import unittest
from pathlib import Path
from snakekb import sources
from snakekb.articles import parse_html, ingest
from snakekb.errors import Paused

class ArticlesTests(unittest.TestCase):
    def page(self, identity='123'):
        detail = {'id_str': identity, 'basic': {'title': '标题'}, 'modules': [{'module_content': {'paragraphs': [{'text': {'nodes': [{'word': {'words': '正文'}}]}}, {'pic': {'url': 'http://i0.hdslb.com/bfs/a.png'}}]}}]}
        return 'window.__INITIAL_STATE__=' + json.dumps({'detail': detail}) + ';'

    def test_url(self):
        info = sources.parse('https://www.bilibili.com/opus/123?spm_id_from=x')
        self.assertEqual(info, sources.parse('opus123'))
        self.assertEqual(info['url'], 'https://www.bilibili.com/opus/123')

    def test_content(self):
        article = parse_html(self.page(), '123')
        self.assertEqual(article['paragraphs'][0], '正文')
        self.assertEqual(len(article['images']), 1)
        with self.assertRaises(Paused):
            parse_html(self.page(), '456')
        with self.assertRaises(Paused):
            parse_html('<html>login</html>', '123')

    def test_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = ingest(Path(tmp), sources.parse('opus123'), html=self.page())
            self.assertTrue((entry / 'notes.md').exists())
            index = json.loads((Path(tmp) / 'formal/index.json').read_text())
            self.assertEqual(index['opus123'], entry.name)

    def test_formula_and_rich_text(self):
        html = self.page().replace('"words": "', '"words": "')
        state = {'detail': {'id_str': '123', 'modules': [{'module_content': {'paragraphs': [{'text': {'nodes': [{'formula': {'latex_content': 'x+y'}}, {'rich': {'text': 'link label'}}]}}]}}]}}
        result = parse_html('window.__INITIAL_STATE__=' + json.dumps(state), '123')
        self.assertEqual(result['paragraphs'], ['$x+y$link label'])
