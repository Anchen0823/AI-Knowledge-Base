import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from snakekb import pipeline as p
from snakekb.diagnostics import decode_html, diagnostic_snapshot, interpret_existing


class DiagnosticsTests(unittest.TestCase):
    def test_charset_fix(self):
        body = '<meta charset="utf-8"><title>出错啦! - bilibili.com</title>'.encode()
        self.assertIn("出错啦", decode_html(body, "text/html"))
        self.assertEqual(diagnostic_snapshot(412, {"Content-Type": "text/html"}, body)["page_title"], "出错啦! - bilibili.com")

    def test_412_not_generalized(self):
        result = interpret_existing({"target": p.CANONICAL, "status_code": 412})
        self.assertEqual(result["subtitle"], "not_tested")
        self.assertFalse(result["redirect_setting_explains_412"])
        self.assertIn("unresolved", result["root_cause"])

    def test_secret_headers_not_retained(self):
        record = diagnostic_snapshot(403, {"Set-Cookie": "secret", "Authorization": "secret", "Content-Type": "text/html"}, b"<p>access_token=secret</p>")
        self.assertNotIn("secret", json.dumps(record))

    def test_redirect_location_redacted(self):
        result = diagnostic_snapshot(302, {"Location": "https://example.com/a?token=private"}, b"")
        self.assertEqual(result["redirect_host"], "example.com")
        self.assertNotIn("private", json.dumps(result))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.test_base = (Path(__file__).resolve().parents[1] / "validation" / "offline-test-tmp").resolve()
        self.test_base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.test_base)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.assertEqual(self.root.resolve().parent, self.test_base)
        self.temp.cleanup()

    def test_known_link_mapping_without_network(self):
        for source in [p.BVID, p.CANONICAL, "https://b23.tv/Zemg4oc", "https://www.bilibili.com/video/"+p.BVID+"?p=1"]:
            self.assertEqual(p.normalize(source), p.BVID)

    def test_other_part_and_host_rejected(self):
        for source in [p.CANONICAL+"?p=2", "https://evil.example/video/"+p.BVID,
                       "https://www.bilibili.com.evil.example/video/"+p.BVID]:
            with self.assertRaises(p.Paused):
                p.normalize(source)

    def test_caption_timestamps_and_no_truncation(self):
        path = self.root / "captions.json"
        body = "长文本"*6000
        p.write(path, {"body": [{"from": 0, "to": 3, "content": body}]})
        result = p.parse_captions(path, 3)
        self.assertEqual(result[0]["text"], body)
        self.assertEqual(result[0]["end"], 3)

    def test_srt_vtt(self):
        path = self.root / "captions.srt"
        path.write_text("1\n00:00:00,000 --> 00:00:01,500\n测试\n\n2\n00:00:02,000 --> 00:00:03,000\n第二条\n", encoding="utf-8")
        self.assertEqual(p.parse_captions(path, 3)[1]["start"], 2)
        path = self.root / "captions.vtt"
        path.write_text("WEBVTT\n\n00:00.000 --> 00:03.000 align:start\n<b>测试</b>\n", encoding="utf-8")
        self.assertEqual(p.parse_captions(path, 3)[0]["text"], "测试")

    def test_invalid_captions_rejected(self):
        for item in [{"start": 3, "end": 2, "text": "bad"},
                     {"start": 0, "end": 5, "text": "bad"}]:
            path = self.root / "bad.json"
            p.write(path, [item])
            with self.assertRaises(p.Paused):
                p.parse_captions(path, 3)

    def test_gap_detection_and_silence(self):
        self.assertEqual(p.gaps([(0, 1), (3, 5)], 6), [[1, 3], [5, 6]])
        self.assertEqual(p.silence_ranges("silence_start: 1\nsilence_end: 3\nsilence_start: 5", 6), [[1, 3], [5, 6]])

    def test_missing_material_never_runs_process(self):
        with patch.object(p.subprocess, "run") as run:
            with self.assertRaises(p.Paused):
                p.prepare(self.root, p.BVID, None, None, None)
            run.assert_not_called()
        self.assertIn("没有通过门禁", p.query(self.root))
        self.assertFalse((self.root / "formal").exists())

    def test_answer_reference_and_uncertainty_gate(self):
        batch = {"captions": [{"id": "s0000"}], "frames": [{"id": "f0000"}]}
        answer = {"summary": "测试", "reviewed_caption_ids": ["s0000"], "reviewed_frame_ids": ["f0000"],
                  "uncertainties": [], "claims": [{"kind": "author_claim", "text": "条件测试", "conditions": [], "source_ids": ["s0000"]}],
                  "condition_audit": [{"topic": t, "status": "not_in_this_batch", "detail": "", "source_ids": []}
                                      for t in ["grid_parity", "edge_wrapping", "performance_scope"]]}
        self.assertEqual(p.validate_answer(answer, batch), [])
        bad = copy.deepcopy(answer)
        bad["claims"][0]["source_ids"] = ["invented"]
        self.assertTrue(p.validate_answer(bad, batch))
        bad = copy.deepcopy(answer)
        bad["uncertainties"] = ["画面读不清"]
        self.assertTrue(p.validate_answer(bad, batch))
        bad = copy.deepcopy(answer)
        bad["reviewed_frame_ids"] = []
        self.assertTrue(p.validate_answer(bad, batch))

    def test_pending_marker_not_leaked_to_query(self):
        pending = p.initialize(self.root)
        (pending / "private.txt").write_text("PENDING_SECRET_MARKER", encoding="utf-8")
        self.assertNotIn("PENDING_SECRET_MARKER", p.query(self.root))


if __name__ == "__main__":
    unittest.main()
