import tempfile
import unittest
from pathlib import Path

from src import profiles, subtitles


class VerticalSubtitleTests(unittest.TestCase):
    def test_vertical_video_uses_portrait_canvas_and_places_subtitles_below_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = subtitles.write_ass(
                [{"start": 0.0, "end": 1.0, "zh": "这是一句字幕。"}],
                Path(tmp),
                style={
                    "position": "top",
                    "fontsize": 64,
                    "play_res_x": 1080,
                    "play_res_y": 1920,
                    "marginv": 330,
                },
            )
            content = path.read_text(encoding="utf-8")

        self.assertIn("PlayResX: 1080", content)
        self.assertIn("PlayResY: 1920", content)
        self.assertIn(",8,90,90,330,1", content)

    def test_portrait_subtitles_wrap_to_fit_the_visible_width(self):
        text = (
            "可能听说过我，知道我总是在探讨两个核心问题："
            "如何不靠运气获得财富，以及如何获得真正的幸福。"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = subtitles.write_ass(
                [{"start": 0.0, "end": 1.0, "zh": text}],
                Path(tmp),
                style={
                    "position": "bottom",
                    "fontsize": 64,
                    "play_res_x": 1080,
                    "play_res_y": 1920,
                    "marginv": 180,
                },
            )
            content = path.read_text(encoding="utf-8")

        dialogue = next(
            line for line in content.splitlines() if line.startswith("Dialogue:")
        )
        rendered = dialogue.split(",,", 1)[1]
        lines = rendered.split("\\N")
        self.assertGreaterEqual(len(lines), 3)
        self.assertEqual("".join(lines), text)
        self.assertTrue(all(len(line) <= 13 for line in lines))
        self.assertIn(",2,90,90,180,1", content)

    def test_naval_wide_wrap_fills_the_first_line_without_changing_default_wrap(self):
        text = "我要把冥想从那个遥不可及的山顶上拉下来，"
        common_style = {
            "position": "bottom",
            "fontsize": 64,
            "play_res_x": 1080,
            "play_res_y": 1920,
            "marginv": 180,
        }
        with tempfile.TemporaryDirectory() as tmp:
            balanced_dir = Path(tmp) / "balanced"
            wide_dir = Path(tmp) / "wide"
            balanced_dir.mkdir()
            wide_dir.mkdir()
            balanced_path = subtitles.write_ass(
                [{"start": 0.0, "end": 1.0, "zh": text}],
                balanced_dir,
                style=common_style,
            )
            wide_path = subtitles.write_ass(
                [{"start": 0.0, "end": 1.0, "zh": text}],
                wide_dir,
                style={**common_style, "wrap_mode": "wide"},
            )
            balanced_content = balanced_path.read_text(encoding="utf-8")
            wide_content = wide_path.read_text(encoding="utf-8")

        balanced_dialogue = next(
            line
            for line in balanced_content.splitlines()
            if line.startswith("Dialogue:")
        )
        wide_dialogue = next(
            line for line in wide_content.splitlines() if line.startswith("Dialogue:")
        )
        balanced_lines = balanced_dialogue.split(",,", 1)[1].split("\\N")
        wide_lines = wide_dialogue.split(",,", 1)[1].split("\\N")

        self.assertEqual([len(line) for line in balanced_lines], [10, 10])
        self.assertEqual([len(line) for line in wide_lines], [13, 7])
        self.assertEqual("".join(wide_lines), text)

    def test_naval_fixed_wrap_breaks_thirty_characters_into_two_fifteen_char_lines(self):
        text = ("甲" * 13) + "，" + ("乙" * 16)
        with tempfile.TemporaryDirectory() as tmp:
            path = subtitles.write_ass(
                [{"start": 0.0, "end": 1.0, "zh": text}],
                Path(tmp),
                style={
                    "position": "bottom",
                    "fontsize": 64,
                    "play_res_x": 1080,
                    "play_res_y": 1920,
                    "marginv": 180,
                    "wrap_mode": "fixed",
                    "max_chars_per_line": 15,
                },
            )
            content = path.read_text(encoding="utf-8")

        dialogue = next(
            line for line in content.splitlines() if line.startswith("Dialogue:")
        )
        lines = dialogue.split(",,", 1)[1].split("\\N")
        self.assertEqual([len(line) for line in lines], [15, 15])
        self.assertEqual("".join(lines), text)

    def test_jung_default_wrap_keeps_a_long_horizontal_cue_to_two_lines(self):
        text = (
            "这个时刻很少以爆发的方式出现，"
            "它通常以疲劳的形式到来，"
            "一种睡眠无法修复的疲倦，"
        )
        cfg = profiles.apply_step_defaults("compose", {"profile": "jung"})
        with tempfile.TemporaryDirectory() as tmp:
            path = subtitles.write_ass(
                [{"start": 0.0, "end": 1.0, "zh": text}],
                Path(tmp),
                style={
                    "position": cfg["position"],
                    "fontsize": cfg["fontsize"],
                    "play_res_x": cfg["canvas_width"],
                    "play_res_y": cfg["canvas_height"],
                    "marginv": cfg["subtitle_marginv"],
                    "wrap_mode": cfg["subtitle_wrap_mode"],
                    "max_chars_per_line": cfg["subtitle_max_chars_per_line"],
                },
            )
            content = path.read_text(encoding="utf-8")

        dialogue = next(
            line for line in content.splitlines() if line.startswith("Dialogue:")
        )
        lines = dialogue.split(",,", 1)[1].split("\\N")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines, [
            "这个时刻很少以爆发的方式出现，",
            "它通常以疲劳的形式到来，一种睡眠无法修复的疲倦，",
        ])
        self.assertTrue(all(len(line) <= 24 for line in lines))
        self.assertEqual("".join(lines), text)


if __name__ == "__main__":
    unittest.main()
