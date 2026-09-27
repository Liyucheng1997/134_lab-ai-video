import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import scene_art


def _segs(n, step=5.0, text="这是一句话。"):
    return [{"start": i * step, "end": i * step + step - 0.4, "zh": text} for i in range(n)]


class SplitScenesTests(unittest.TestCase):
    def test_groups_sentences_into_scenes_of_roughly_target_length(self):
        scenes = scene_art.split_scenes(_segs(36), scene_seconds=40, max_scenes=48)
        self.assertEqual(len(scenes), 4)
        self.assertEqual(scenes[0]["start"], 0)
        self.assertEqual([s["index"] for s in scenes], [0, 1, 2, 3])
        self.assertTrue(all("_segs" not in s for s in scenes))

    def test_long_video_widens_scenes_to_respect_max_scenes(self):
        scenes = scene_art.split_scenes(_segs(720), scene_seconds=20, max_scenes=30)
        self.assertLessEqual(len(scenes), 31)

    def test_short_tail_is_merged_into_previous_scene(self):
        scenes = scene_art.split_scenes(_segs(9), scene_seconds=40, max_scenes=48)
        self.assertEqual(len(scenes), 1)
        self.assertAlmostEqual(scenes[0]["end"], 44.6)


class SanitizeSvgTests(unittest.TestCase):
    def test_forces_canvas_size_and_strips_unsafe_content(self):
        raw = ('好的：<svg viewBox="0 0 10 10" width="10" onload="x()">'
               '<script>alert(1)</script><text>字</text>'
               '<image href="https://evil/a.png"/><rect width="5" height="5"/></svg> 完成')
        svg = scene_art._sanitize_svg(raw, 1920, 1080)
        self.assertTrue(svg.startswith("<svg "))
        self.assertIn('width="1920"', svg)
        self.assertIn('height="1080"', svg)
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', svg)
        for bad in ("script", "onload", "<text", "<image", "https://"):
            self.assertNotIn(bad, svg)
        self.assertIn("<rect", svg)

    def test_rejects_broken_svg(self):
        with self.assertRaises(Exception):
            scene_art._sanitize_svg("<svg><g></svg>", 1920, 1080)
        with self.assertRaises(ValueError):
            scene_art._sanitize_svg("no drawing here", 1920, 1080)


class GenerateScenesTests(unittest.TestCase):
    def _fake_renderer(self, fail_loop=False):
        class FakeRenderer:
            def __init__(self, w, h):
                pass

            def render(self, svg, style, out_png, out_loop):
                out_png.write_bytes(b"png")
                if out_loop is not None:
                    if fail_loop:
                        raise RuntimeError("录制失败")
                    out_loop.write_bytes(b"mp4")

            def close(self):
                pass
        return FakeRenderer

    def test_failed_scenes_reuse_neighbour_and_drawn_scenes_are_cached(self):
        calls = []

        def fake_draw(style, text, *, topic, index, total, w, h, animate):
            calls.append(index)
            if index == 0:
                raise RuntimeError("boom")
            return f"构思{index}", '<svg xmlns="http://www.w3.org/2000/svg"></svg>'

        segs = [{"start": i * 5.0, "end": i * 5.0 + 4.6, "zh": f"第{i}句。"} for i in range(24)]
        with tempfile.TemporaryDirectory() as tmp,                 patch.object(scene_art, "animation_available", return_value=True),                 patch.object(scene_art, "draw_svg", side_effect=fake_draw),                 patch.object(scene_art, "Renderer", self._fake_renderer()):
            wd = Path(tmp)
            scenes = scene_art.generate_scenes(segs, wd, style="ink", scene_seconds=40,
                                               concurrency=2, time_budget_min=5)
            self.assertEqual(len(scenes), 3)
            self.assertEqual(scenes[0]["status"], "failed")
            self.assertEqual(scenes[0]["png"], scenes[1]["png"])
            self.assertEqual(scenes[0]["loop"], scenes[1]["loop"])
            self.assertTrue(scenes[1]["loop"].endswith(".mp4"))
            self.assertTrue((wd / "scenes" / "scenes.json").is_file())

            calls.clear()
            again = scene_art.generate_scenes(segs, wd, style="ink", scene_seconds=40,
                                              concurrency=2, time_budget_min=5)
            self.assertEqual(calls, [0, 0])           # 只有失败的场景重画
            self.assertEqual([s["status"] for s in again[1:]], ["cached", "cached"])

    def test_loop_recording_failure_falls_back_to_still_without_redrawing(self):
        calls = []

        def fake_draw(style, text, *, topic, index, total, w, h, animate):
            calls.append(index)
            return "构思", '<svg xmlns="http://www.w3.org/2000/svg"></svg>'

        with tempfile.TemporaryDirectory() as tmp,                 patch.object(scene_art, "animation_available", return_value=True),                 patch.object(scene_art, "draw_svg", side_effect=fake_draw),                 patch.object(scene_art, "Renderer", self._fake_renderer(fail_loop=True)):
            scenes = scene_art.generate_scenes(_segs(8), Path(tmp), concurrency=1,
                                               time_budget_min=5)
            self.assertEqual(calls, [0])
            self.assertEqual(scenes[0]["status"], "drawn")
            self.assertIsNone(scenes[0]["loop"])

    def test_static_mode_has_no_loop(self):
        with tempfile.TemporaryDirectory() as tmp,                 patch.object(scene_art, "draw_svg",
                             return_value=("构思", '<svg xmlns="http://www.w3.org/2000/svg"></svg>')),                 patch.object(scene_art, "Renderer", self._fake_renderer()):
            scenes = scene_art.generate_scenes(_segs(8), Path(tmp), concurrency=1,
                                               time_budget_min=5, animate=False)
            self.assertIsNone(scenes[0]["loop"])

    def test_all_failures_raise(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(scene_art, "draw_svg", side_effect=RuntimeError("未登录")):
            with self.assertRaisesRegex(RuntimeError, "未登录"):
                scene_art.generate_scenes(_segs(10), Path(tmp), concurrency=1, time_budget_min=5)

    def test_unknown_style_falls_back_to_default(self):
        self.assertEqual(scene_art.normalize_style("nope"), scene_art.DEFAULT_STYLE)
        self.assertEqual(scene_art.normalize_style("INK"), "ink")


if __name__ == "__main__":
    unittest.main()
