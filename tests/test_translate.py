import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import translate


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{
                "message": {"content": json.dumps(self._payload, ensure_ascii=False)}
            }]
        }


class WholeArticleTranslationTests(unittest.TestCase):
    def setUp(self):
        self.source = [
            {"start": 2.0, "end": 3.0, "text": "When you stop"},
            {"start": 3.2, "end": 5.0, "text": "waiting for permission, your life begins."},
            {"start": 5.1, "end": 7.0, "text": "Visit example.com and buy my course."},
        ]

    def test_default_rewrite_level_is_deep_polish(self):
        self.assertEqual(translate._normalize_rewrite_level(None), "high")

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_medium_rewrite_uses_one_clean_chinese_article_without_english_fallback(self, post):
        post.return_value = _FakeResponse({
            "article_zh": "当你不再等待别人的许可，人生才真正开始。",
            "ad_removed": True,
            "ad_notes": "已删除课程购买引导",
        })

        with tempfile.TemporaryDirectory() as tmp:
            result = translate.translate(
                self.source,
                Path(tmp),
                engine="deepseek",
                rewrite_level="medium",
            )

        self.assertEqual("".join(item["zh"] for item in result),
                         "当你不再等待别人的许可，人生才真正开始。")
        self.assertNotRegex("".join(item["zh"] for item in result), r"[A-Za-z]{3,}")
        self.assertEqual(post.call_count, 1)
        request_payload = post.call_args.kwargs["json"]
        user_payload = json.loads(request_payload["messages"][1]["content"])
        self.assertEqual(
            user_payload["source_article_en"],
            "When you stop waiting for permission, your life begins. "
            "Visit example.com and buy my course.",
        )
        self.assertNotIn("lines", user_payload)

    def test_chinese_article_is_split_then_retimed_monotonically(self):
        sentences = translate._split_zh_article(
            "第一句话完整结束。第二句话比较长，但仍然应该按照中文语义自然划分！"
        )
        result = translate._retime_article_segments(sentences, self.source)

        self.assertEqual([item["zh"] for item in result], sentences)
        self.assertEqual(result[0]["start"], 2.0)
        self.assertEqual(result[-1]["end"], 7.0)
        self.assertTrue(all(a["end"] <= b["start"] for a, b in zip(result, result[1:])))

    def test_sentence_closing_quote_stays_with_previous_sentence(self):
        self.assertEqual(
            translate._split_zh_article("他说：“别再等待。”然后继续前进。", max_chars=0),
            ["他说：“别再等待。”", "然后继续前进。"],
        )

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_deep_polish_prompt_requires_spoken_short_chinese_sentences(self, post):
        post.return_value = _FakeResponse({
            "article_zh": "“先把事情看清楚”——这是第一步；再决定下一步怎么走。这样更容易坚持下去。",
            "ad_removed": False,
            "ad_notes": "",
        })

        with tempfile.TemporaryDirectory() as tmp:
            result = translate.translate(
                self.source, Path(tmp), engine="deepseek", rewrite_level="深度顺稿"
            )

        request_payload = post.call_args.kwargs["json"]
        system_prompt = request_payload["messages"][0]["content"]
        user_payload = json.loads(request_payload["messages"][1]["content"])
        self.assertEqual(user_payload["rewrite_level"], "high")
        self.assertIn("拆成两到四个简单中文短句", system_prompt)
        self.assertIn("一个句子只表达一个重点", system_prompt)
        self.assertIn("这不是摘要", system_prompt)
        self.assertIn("十二到三十个中文字", system_prompt)
        self.assertIn("不要使用任何引号", system_prompt)
        self.assertIn("不要使用破折号", system_prompt)
        spoken_text = "".join(item["zh"] for item in result)
        self.assertNotRegex(spoken_text, r"[“”\"—–―：:；;…]")
        self.assertIn("先把事情看清楚，这是第一步，再决定下一步怎么走。", spoken_text)

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_untranslated_english_triggers_whole_article_retry(self, post):
        post.side_effect = [
            _FakeResponse({
                "article_zh": "这里仍然留下了 this is a whole untranslated English sentence。",
                "ad_removed": False,
                "ad_notes": "",
            }),
            _FakeResponse({
                "article_zh": "当你不再等待别人的许可，人生才真正开始。",
                "ad_removed": True,
                "ad_notes": "已删除购买引导",
            }),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            result = translate.translate(
                self.source, Path(tmp), engine="deepseek", rewrite_level="medium"
            )
            meta = json.loads(Path(tmp, "translated.meta.json").read_text(encoding="utf-8"))

        self.assertEqual(post.call_count, 2)
        self.assertEqual(meta["advertising"]["quality_retry_count"], 1)
        self.assertNotRegex("".join(item["zh"] for item in result),
                            r"whole untranslated English")

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_invalid_model_output_never_falls_back_to_source_english(self, post):
        post.side_effect = [
            _FakeResponse({"article_zh": "this remains entirely in English text"}),
            _FakeResponse({"article_zh": "another untranslated English response here"}),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "连续两次"):
                translate.translate(
                    self.source, work_dir, engine="deepseek", rewrite_level="medium"
                )
            self.assertFalse((work_dir / "translated.json").exists())


if __name__ == "__main__":
    unittest.main()
