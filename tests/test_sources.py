"""来源登记与「加一条新链接」这条路径。"""
import json
from pathlib import Path
import re
import tempfile
import unittest

from snakekb import pipeline as p
from snakekb import sources


class ParseTests(unittest.TestCase):
    def test_any_bilibili_video_url_is_accepted(self):
        for value in ['https://www.bilibili.com/video/BV1WWNx6tcd5/',
                      'https://www.bilibili.com/video/BV1WWNx6tcd5',
                      'https://www.bilibili.com/zh-Hans/video/BV1WWNx6tcd5/',
                      'http://m.bilibili.com/video/BV1WWNx6tcd5/?spm_id_from=333.788',
                      'BV1WWNx6tcd5']:
            self.assertEqual(sources.parse(value)['bvid'], 'BV1WWNx6tcd5', value)

    def test_other_sites_and_extra_parts_are_rejected(self):
        for value in ['https://evil.example/video/BV1WWNx6tcd5',
                      'https://www.bilibili.com.evil.example/video/BV1WWNx6tcd5',
                      'https://www.bilibili.com/space/BV1WWNx6tcd5',
                      'https://www.bilibili.com/video/BV1WWNx6tcd5?p=2',
                      'not a link']:
            with self.assertRaises(p.Paused, msg=value):
                sources.parse(value)

    def test_empty_input_says_what_to_paste(self):
        with self.assertRaises(p.Paused) as caught:
            sources.parse('   ')
        self.assertIn('BV', str(caught.exception))

    def test_unknown_short_link_is_marked_for_resolution(self):
        info = sources.parse('https://b23.tv/abcdefg')
        self.assertEqual(info['kind'], 'short')
        self.assertIsNone(info['bvid'])


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=p.ROOT / 'validation/offline-test-tmp')
        self.data = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_new_source_starts_unpinned(self):
        profile = sources.profile(self.data, 'BV1WWNx6tcd5')
        self.assertEqual(profile.display_title, 'BV1WWNx6tcd5')
        self.assertIsNone(profile.duration)   # 不拿固定时长卡没见过的视频
        self.assertEqual(profile.topics, ())  # 也不预设要核对哪些条件
        self.assertEqual(profile.media, 'auto')

    def test_remember_stores_title_and_duration(self):
        sources.remember(self.data, 'BV1WWNx6tcd5', title='一条新视频', duration=93.4)
        written = json.loads((self.data / 'sources.json').read_text(encoding='utf-8'))
        self.assertEqual(written['BV1WWNx6tcd5']['duration'], 93.4)
        profile = sources.profile(self.data, 'BV1WWNx6tcd5')
        self.assertEqual(profile.display_title, '一条新视频')
        self.assertEqual(profile.duration, 93.4)

    def test_builtin_sample_cannot_be_overwritten(self):
        sources.remember(self.data, 'BV1QTHA6DELc', title='改个名', duration=1)
        self.assertFalse((self.data / 'sources.json').exists())
        self.assertEqual(sources.profile(self.data, 'BV1QTHA6DELc').duration, 485.0)

    def test_broken_registry_is_reported_with_fix(self):
        (self.data / 'sources.json').write_text('{oops', encoding='utf-8')
        with self.assertRaises(p.Paused) as caught:
            sources.profile(self.data, 'BV1WWNx6tcd5')
        self.assertIn('sources.json', str(caught.exception))


class GateTests(unittest.TestCase):
    """样例把条件主题固定住，新来源不预设主题。"""

    batch = {'captions': [{'id': 's0000'}], 'frames': [{'id': 'f0000'}]}

    def answer(self, audits):
        return {'summary': '测试', 'reviewed_caption_ids': ['s0000'], 'reviewed_frame_ids': ['f0000'],
                'uncertainties': [], 'source_limitations': [],
                'claims': [{'kind': 'author_claim', 'text': '一句话', 'conditions': [],
                            'source_ids': ['s0000']}],
                'condition_audit': audits}

    def test_sample_still_requires_its_three_topics(self):
        with p.use_source(p.DEFAULT_DATA, 'BV1QTHA6DELc'):
            schema = p.output_schema()
            enum = schema['properties']['condition_audit']['items']['properties']['topic']['enum']
            self.assertEqual(set(enum), {'grid_parity', 'edge_wrapping', 'performance_scope'})
            missing = self.answer([{'topic': 'grid_parity', 'status': 'not_in_this_batch',
                                    'detail': '', 'source_ids': []}])
            self.assertTrue(p.validate_answer(missing, self.batch))

    def test_new_source_accepts_its_own_topics(self):
        with p.use_source(p.DEFAULT_DATA, 'BV1WWNx6tcd5'):
            self.assertEqual(p.output_schema()['properties']['condition_audit']
                             ['items']['properties']['topic'], {'type': 'string'})
            found = self.answer([{'topic': 'price_on_screen', 'status': 'observed',
                                  'detail': '画面标了价格', 'source_ids': ['f0000']}])
            self.assertEqual(p.validate_answer(found, self.batch), [])
            nameless = self.answer([{'topic': '  ', 'status': 'observed',
                                     'detail': 'x', 'source_ids': ['f0000']}])
            self.assertTrue(p.validate_answer(nameless, self.batch))

    def test_many_conditions_are_allowed_but_real_gaps_still_block(self):
        with p.use_source(p.DEFAULT_DATA, 'BV1WWNx6tcd5'):
            audits = [{'topic': f'topic_{i}', 'status': 'observed', 'detail': 'condition', 'source_ids': ['f0000']} for i in range(20)]
            answer = self.answer(audits)
            self.assertEqual(p.validate_answer(answer, self.batch), [])
            self.assertNotIn('maxItems', p.output_schema()['properties']['condition_audit'])
            answer['uncertainties'] = ['Key evidence is unreadable']
            self.assertTrue(p.validate_answer(answer, self.batch))
            answer['uncertainties'] = []
            answer['condition_audit'][0]['source_ids'] = ['outside_batch']
            self.assertTrue(p.validate_answer(answer, self.batch))

    def test_prompt_switches_with_source(self):
        with p.use_source(p.DEFAULT_DATA, 'BV1QTHA6DELc'):
            self.assertIn('网格', p.make_prompt(self.batch))
        with p.use_source(p.DEFAULT_DATA, 'BV1WWNx6tcd5'):
            prompt = p.make_prompt(self.batch)
            self.assertNotIn('网格', prompt)
            self.assertIn('condition_audit', prompt)


class SnapshotTests(unittest.TestCase):
    def test_reading_state_does_not_create_directories(self):
        from snakekb.web import snapshot
        with tempfile.TemporaryDirectory(dir=p.ROOT / 'validation/offline-test-tmp') as temp:
            data = Path(temp)
            state = snapshot(data)
            self.assertEqual(state['library'], [])
            self.assertEqual(state['jobs'], [])
            self.assertFalse((data / 'pending').exists())


if __name__ == '__main__':
    unittest.main()
