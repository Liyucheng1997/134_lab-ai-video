import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from src import steps


class TranslateStepTests(unittest.TestCase):
    def test_portrait_segment_limit_is_forwarded_to_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            source = [{"start": 0.0, "end": 1.0, "text": "中文原稿。"}]
            (work_dir / "segments.json").write_text(
                json.dumps({"language": "zh", "segments": source}, ensure_ascii=False),
                encoding="utf-8",
            )
            with (
                patch.object(steps, "work_dir_of", return_value=work_dir),
                patch.object(
                    steps.translate_mod,
                    "translate",
                    return_value=[{**source[0], "zh": "改写结果。"}],
                ) as translate_article,
            ):
                steps.run_translate("job_test", {
                    "engine": "deepseek",
                    "rewrite_level": "high",
                    "max_zh_segment_chars": 30,
                    "profile": "naval",
                })

        self.assertEqual(
            translate_article.call_args.kwargs["max_zh_segment_chars"],
            30,
        )
        self.assertEqual(
            translate_article.call_args.kwargs["profile"],
            "naval",
        )


class ComposeStepTests(unittest.TestCase):
    def test_uploaded_image_is_used_as_the_video_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            image = work_dir / "uploaded-background.png"
            image.write_bytes(b"test-image")
            (work_dir / "dub.wav").write_bytes(b"test-audio")
            segments = [{
                "start": 0.0,
                "end": 1.0,
                "text": "source",
                "zh": "中文字幕",
            }]

            def fake_compose(**kwargs):
                kwargs["out_path"].write_bytes(b"test-video")
                return kwargs["out_path"]

            with (
                patch.object(steps, "work_dir_of", return_value=work_dir),
                patch.object(steps, "_apply_compose_audio_speed",
                             return_value=segments),
                patch.object(steps.subtitles, "build") as build_subtitles,
                patch.object(steps.compose_mod, "compose",
                             side_effect=fake_compose) as compose,
            ):
                result = steps.run_compose("job_test", {
                    "mode": "image",
                    "file": str(image),
                    "burn": True,
                    "subtitle_wrap_mode": "fixed",
                    "subtitle_max_chars_per_line": 15,
                })

        self.assertEqual(result["mode"], "image")
        self.assertEqual(compose.call_args.kwargs["image"], image)
        self.assertEqual(
            build_subtitles.call_args.kwargs["style"]["wrap_mode"],
            "fixed",
        )
        self.assertEqual(
            build_subtitles.call_args.kwargs["style"]["max_chars_per_line"],
            15,
        )

    def test_image_mode_auto_generates_a_main_title_for_the_video_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            image = work_dir / "naval.png"
            image.write_bytes(b"test-image")
            (work_dir / "dub.wav").write_bytes(b"test-audio")
            (work_dir / "translated.json").write_text(json.dumps([
                {"start": 0.0, "end": 1.0, "text": "source", "zh": "停止追逐地位，开始积累自由。"}
            ], ensure_ascii=False), encoding="utf-8")
            segments = [{
                "start": 0.0,
                "end": 1.0,
                "text": "source",
                "zh": "停止追逐地位，开始积累自由。",
            }]

            def fake_compose(**kwargs):
                kwargs["out_path"].write_bytes(b"test-video")
                return kwargs["out_path"]

            with (
                patch.object(steps, "work_dir_of", return_value=work_dir),
                patch.object(steps, "_apply_compose_audio_speed",
                             return_value=segments),
                patch.object(steps.subtitles, "build"),
                patch.object(steps.publish_mod, "gen_title",
                             return_value="真正的财富，是拥有自由"),
                patch.object(steps.compose_mod, "compose",
                             side_effect=fake_compose) as compose,
            ):
                steps.run_compose("job_test", {
                    "mode": "image",
                    "image": str(image),
                    "image_title_enabled": True,
                    "image_title": "",
                })

            self.assertEqual(compose.call_args.kwargs["title"],
                             "真正的财富，是拥有自由")
            self.assertEqual(
                (work_dir / "image_title.txt").read_text(encoding="utf-8"),
                "真正的财富，是拥有自由",
            )


class PublishStepTests(unittest.TestCase):
    def test_naval_header_is_forwarded_to_the_vertical_cover_generator(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            final_video = work_dir / "final.mp4"
            with (
                patch.object(steps, "work_dir_of", return_value=work_dir),
                patch.object(steps, "final_path", return_value=final_video),
                patch.object(
                    steps.publish_mod,
                    "prepare",
                    return_value={},
                ) as prepare,
            ):
                steps.run_publish("job_test", {
                    "profile": "naval",
                    "douyin_cover_header": "纳瓦尔宝典",
                })

        self.assertEqual(
            prepare.call_args.kwargs["douyin_cover_header"],
            "纳瓦尔宝典",
        )


if __name__ == "__main__":
    unittest.main()
