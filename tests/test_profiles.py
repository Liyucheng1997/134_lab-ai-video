import unittest

from src import profiles


class CreationProfileTests(unittest.TestCase):
    def test_naval_profile_supplies_the_requested_pipeline_defaults(self):
        configs = profiles.apply_defaults({}, "naval")

        self.assertEqual(configs["asr"]["language"], "zh")
        self.assertEqual(configs["translate"]["rewrite_level"], "high")
        self.assertEqual(configs["translate"]["max_zh_segment_chars"], 30)
        self.assertEqual(configs["tts"]["voice"], "大一嘉豪哥")
        self.assertEqual(configs["compose"]["mode"], "image")
        self.assertEqual(configs["compose"]["canvas_width"], 1080)
        self.assertEqual(configs["compose"]["canvas_height"], 1920)
        self.assertEqual(
            configs["compose"]["image_title"],
            "关注我，和纳瓦尔一起成长",
        )
        self.assertEqual(configs["compose"]["image_title_font_size"], 110)
        self.assertEqual(configs["compose"]["image_title_width"], 0.84)
        self.assertEqual(configs["compose"]["image_title_x"], 0.10)
        self.assertEqual(configs["compose"]["image_title_y"], 0.13)
        self.assertEqual(configs["compose"]["position"], "middle")
        self.assertEqual(configs["compose"]["subtitle_marginv"], 0)
        self.assertEqual(configs["compose"]["subtitle_wrap_mode"], "fixed")
        self.assertEqual(configs["compose"]["subtitle_max_chars_per_line"], 15)
        self.assertTrue(configs["compose"]["image"].endswith("pictures\\02_纳瓦尔.png"))
        self.assertTrue(configs["publish"]["cover_image"].endswith("pictures\\02_纳瓦尔.png"))
        self.assertEqual(configs["publish"]["douyin_cover_header"], "纳瓦尔宝典")
        self.assertEqual(configs["publish"]["archive_profile"], "naval")

    def test_jung_profile_preserves_the_existing_defaults(self):
        configs = profiles.apply_defaults({}, "jung")

        self.assertEqual(configs["asr"]["language"], "en")
        self.assertEqual(configs["translate"]["rewrite_level"], "high")
        self.assertEqual(configs["tts"]["voice"], "人工老龙凤")
        self.assertEqual(configs["tts"]["f5_parallel"], 4)
        self.assertEqual(configs["compose"]["mode"], "original")
        self.assertEqual(configs["compose"]["subtitle_wrap_mode"], "balanced")
        self.assertEqual(configs["compose"]["subtitle_max_chars_per_line"], 24)
        self.assertTrue(configs["publish"]["cover_image"].endswith("pictures\\01_荣格心理学.png"))
        self.assertEqual(configs["publish"]["archive_profile"], "jung")


if __name__ == "__main__":
    unittest.main()
