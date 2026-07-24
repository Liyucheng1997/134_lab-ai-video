import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import compose


class ImageTitleComposeTests(unittest.TestCase):
    def test_image_title_is_rendered_with_the_selected_layout_before_video_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            source = work_dir / "naval.png"
            audio = work_dir / "dub.wav"
            output = work_dir / "final.mp4"
            source.write_bytes(b"source")
            audio.write_bytes(b"audio")

            def fake_cover(src, out_png, title, **kwargs):
                out_png.write_bytes(b"image-with-title")
                return out_png

            with (
                patch("src.publish.make_cover_from_image",
                      side_effect=fake_cover) as render_title,
                patch.object(compose, "image_to_video",
                             return_value=output) as image_to_video,
            ):
                compose.compose(
                    mode="image",
                    work_dir=work_dir,
                    audio=audio,
                    ass=None,
                    out_path=output,
                    image=source,
                    title="真正的财富，是拥有自由",
                    title_x=0.54,
                    title_y=0.18,
                    title_font_size=144,
                    title_width=0.38,
                    canvas_width=1080,
                    canvas_height=1920,
                )

        self.assertEqual(
            render_title.call_args.args[:3],
            (source, work_dir / "compose_background.png", "真正的财富，是拥有自由"),
        )
        self.assertEqual(render_title.call_args.kwargs, {
            "x": 0.54,
            "y": 0.18,
            "font_size": 144,
            "box_width": 0.38,
            "target_size": (1080, 1920),
        })
        self.assertEqual(
            image_to_video.call_args.args[0],
            work_dir / "compose_background.png",
        )
        self.assertEqual(image_to_video.call_args.kwargs, {
            "width": 1080,
            "height": 1920,
        })


if __name__ == "__main__":
    unittest.main()
