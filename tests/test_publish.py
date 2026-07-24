import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src import publish


class DouyinCoverTests(unittest.TestCase):
    def test_vertical_cover_renders_the_fixed_header_in_the_top_blank_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            output = root / "douyin-cover.png"
            Image.new("RGB", (1080, 1920), "black").save(source)

            publish.make_douyin_cover_from_image(
                source,
                output,
                "",
                header="纳瓦尔宝典",
            )

            rendered = Image.open(output).convert("RGB")
            upper = rendered.crop((0, 0, 1080, 640))
            has_brand_yellow = any(
                red > 180 and green > 140 and blue < 90
                for red, green, blue in upper.getdata()
            )

        self.assertEqual(rendered.size, (1080, 1920))
        self.assertTrue(has_brand_yellow)


if __name__ == "__main__":
    unittest.main()
