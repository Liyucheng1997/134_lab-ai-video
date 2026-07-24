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

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_deepseek_chinese_output_always_translates_naval_as_chinese_name(self, post):
        post.return_value = _FakeResponse({
            "article_zh": "Naval认为，财富真正带来的不是地位，而是选择生活的自由。",
            "ad_removed": False,
            "ad_notes": "",
        })
        source = [{
            "start": 0.0,
            "end": 3.0,
            "text": "Naval says wealth does not buy status. It buys freedom.",
        }]

        with tempfile.TemporaryDirectory() as tmp:
            result = translate.translate(
                source,
                Path(tmp),
                engine="deepseek",
                rewrite_level="medium",
                max_zh_segment_chars=30,
                profile="naval",
            )

        article = "".join(item["zh"] for item in result)
        self.assertEqual(
            article,
            "纳瓦尔认为，财富真正带来的不是地位，而是选择生活的自由。",
        )
        self.assertNotRegex(article, r"(?i)\bnaval\b")
        request = post.call_args.kwargs["json"]
        request_payload = json.loads(request["messages"][1]["content"])
        self.assertEqual(
            request_payload["required_term_translations"],
            {"Naval": "纳瓦尔"},
        )
        self.assertIn(
            "Naval 或 Naval Ravikant 一律译写为“纳瓦尔”",
            request["messages"][0]["content"],
        )

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_naval_name_rule_is_applied_after_chinese_english_roundtrip(self, post):
        english_bridge = (
            "Naval repeatedly reminds people that real wealth means having "
            "the freedom to decide how to use their own time."
        )
        post.side_effect = [
            _FakeResponse({
                "article_en": english_bridge,
                "ad_removed": False,
                "ad_notes": "",
            }),
            _FakeResponse({
                "article_zh": "Naval认为，能自由支配时间，才算真正拥有财富。",
                "ad_removed": False,
                "ad_notes": "",
            }),
        ]
        source = [{
            "start": 0.0,
            "end": 4.0,
            "text": "Naval反复提醒我们，真正的财富是能够自由安排自己的时间。",
        }]

        with tempfile.TemporaryDirectory() as tmp:
            result = translate.translate(
                source,
                Path(tmp),
                engine="deepseek",
                rewrite_level="medium",
                source_language="zh",
                max_zh_segment_chars=30,
                profile="naval",
            )

        article = "".join(item["zh"] for item in result)
        self.assertEqual(
            article,
            "纳瓦尔认为，能自由支配时间，才算真正拥有财富。",
        )
        bridge_request = post.call_args_list[0].kwargs["json"]
        rewrite_request = post.call_args_list[1].kwargs["json"]
        bridge_payload = json.loads(bridge_request["messages"][1]["content"])
        rewrite_payload = json.loads(rewrite_request["messages"][1]["content"])
        self.assertIn("Naval", bridge_payload["source_article_zh"])
        self.assertNotIn(
            "required_term_translations",
            bridge_payload,
        )
        self.assertEqual(
            rewrite_payload["required_term_translations"],
            {"Naval": "纳瓦尔"},
        )
        self.assertIn(
            "Naval 或 Naval Ravikant 一律译写为“纳瓦尔”",
            rewrite_request["messages"][0]["content"],
        )

    def test_chinese_article_is_split_then_retimed_monotonically(self):
        sentences = translate._split_zh_article(
            "第一句话完整结束。第二句话比较长，但仍然应该按照中文语义自然划分！"
        )
        result = translate._retime_article_segments(sentences, self.source)

        self.assertEqual([item["zh"] for item in result], sentences)
        self.assertEqual(result[0]["start"], 2.0)
        self.assertEqual(result[-1]["end"], 7.0)
        self.assertTrue(all(a["end"] <= b["start"] for a, b in zip(result, result[1:])))

    def test_portrait_voiceover_segments_never_exceed_twenty_one_characters(self):
        article = (
            "你可能听说过我知道我总是在探讨两个核心问题"
            "如何不靠运气获得财富以及如何获得真正的幸福生活。"
        )

        sentences = translate._split_zh_article(article, max_chars=21)

        self.assertEqual("".join(sentences), article)
        self.assertGreater(len(sentences), 2)
        self.assertTrue(all(len(sentence) <= 21 for sentence in sentences))

    def test_portrait_segments_prefer_natural_punctuation_over_fewer_segments(self):
        article = (
            "你可能听说过我，知道我总是在探讨两个核心问题，"
            "如何不靠运气获得财富，以及如何获得真正的幸福。"
        )

        sentences = translate._split_zh_article(article, max_chars=21)

        self.assertEqual(sentences, [
            "你可能听说过我，",
            "知道我总是在探讨两个核心问题，",
            "如何不靠运气获得财富，",
            "以及如何获得真正的幸福。",
        ])

    def test_deep_rewrite_rejects_verbatim_paragraph_reordering(self):
        source = (
            "财富并不是银行卡上的数字，而是你睡觉时仍能创造价值的资产。"
            "真正值得积累的是产品、代码和能够持续工作的系统。"
            "当收入不再完全依赖出卖时间，人才能逐步获得选择生活的自由。"
        )
        reordered = (
            "当收入不再完全依赖出卖时间，人才能逐步获得选择生活的自由。"
            "财富并不是银行卡上的数字，而是你睡觉时仍能创造价值的资产。"
            "真正值得积累的是产品、代码和能够持续工作的系统。"
        )

        issue = translate._article_quality_issue(
            reordered,
            source_article=source,
            similarity_limit=0.82,
            deep_rewrite_checks=True,
        )

        self.assertIn("连续片段照搬率", issue)

    def test_deep_rewrite_rejects_a_long_copied_passage(self):
        copied = "财富并不是银行卡上的数字，而是你睡觉时仍能创造价值的资产"
        source = (
            "有人把高收入误认为财富。"
            + copied
            + "，理解这个区别后，选择工作的方式也会随之改变。"
        )
        rewritten = (
            "工资高不代表真正富有。"
            + copied
            + "。看清资产与工时的差别，职业选择才会发生变化。"
        )

        issue = translate._article_quality_issue(
            rewritten,
            source_article=source,
            similarity_limit=0.82,
            deep_rewrite_checks=True,
        )

        self.assertRegex(issue, r"连续片段照搬率|连续照搬原稿")

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

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_chinese_transcript_is_deep_rewritten_as_chinese_instead_of_translated(self, post):
        english_bridge = (
            "Many people wait for someone else to give them permission to change. "
            "Real change begins when they stop waiting."
        )
        post.side_effect = [
            _FakeResponse({
                "article_en": english_bridge,
                "ad_removed": True,
                "ad_notes": "已在英文中间稿阶段删除关注引导",
            }),
            _FakeResponse({
                "article_zh": "真正的转变，往往始于你不再把决定权交给别人。",
                "ad_removed": False,
                "ad_notes": "",
            }),
        ]
        source = [
            {"start": 0.0, "end": 2.0, "text": "很多人一直在等别人允许自己改变。"},
            {"start": 2.0, "end": 4.0, "text": "其实真正的改变从停止等待开始。"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            result = translate.translate(
                source,
                work_dir,
                engine="deepseek",
                rewrite_level="high",
                max_zh_segment_chars=21,
            )
            meta = json.loads(
                (work_dir / "translated.meta.json").read_text(encoding="utf-8")
            )

        bridge_request = post.call_args_list[0].kwargs["json"]
        bridge_payload = json.loads(
            bridge_request["messages"][1]["content"]
        )
        rewrite_request = post.call_args_list[1].kwargs["json"]
        rewrite_payload = json.loads(
            rewrite_request["messages"][1]["content"]
        )
        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            bridge_payload["task"],
            "translate_chinese_article_to_english_semantic_bridge",
        )
        self.assertIn("source_article_zh", bridge_payload)
        self.assertEqual(
            rewrite_payload["task"],
            "rewrite_chinese_article_from_english_bridge",
        )
        self.assertEqual(rewrite_payload["source_article_en"], english_bridge)
        self.assertNotIn("source_article_zh", rewrite_payload)
        self.assertIn(
            "看不到中文原稿",
            rewrite_request["messages"][0]["content"],
        )
        self.assertEqual(meta["source_language"], "zh")
        self.assertTrue(meta["advertising"]["ad_removed"])
        self.assertIn("关注引导", meta["advertising"]["ad_notes"])
        self.assertEqual("".join(item["zh"] for item in result),
                         "真正的转变，往往始于你不再把决定权交给别人。")

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_chinese_roundtrip_stops_when_english_bridge_contains_chinese(self, post):
        post.return_value = _FakeResponse({
            "article_en": (
                "This bridge still copies 中文原稿中的整句话 and therefore "
                "must never be sent into the Chinese rewrite stage."
            ),
            "ad_removed": False,
            "ad_notes": "",
        })
        source = [
            {
                "start": 0.0,
                "end": 4.0,
                "text": "真正的财富来自不再出售自己的每一分钟。",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            with self.assertRaisesRegex(ValueError, "英文中间稿"):
                translate.translate(
                    source,
                    work_dir,
                    engine="deepseek",
                    rewrite_level="high",
                    source_language="zh",
                    max_zh_segment_chars=21,
                )
            self.assertFalse((work_dir / "translated.json").exists())

        self.assertEqual(post.call_count, 1)

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_chinese_roundtrip_rejects_an_obviously_short_english_bridge(self, post):
        post.return_value = _FakeResponse({
            "article_en": "This is only a short summary.",
            "ad_removed": False,
            "ad_notes": "",
        })
        source_text = "真正的财富来自能持续创造价值的资产，也来自可以自主安排时间的自由。" * 20

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "英文中间稿过短"):
                translate.translate(
                    [{"start": 0.0, "end": 20.0, "text": source_text}],
                    Path(tmp),
                    engine="deepseek",
                    rewrite_level="high",
                    source_language="zh",
                    max_zh_segment_chars=21,
                )

        self.assertEqual(post.call_count, 1)

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_chinese_roundtrip_accepts_a_complete_rewrite_near_original_length(self, post):
        source_text = (
            "很多人误以为收入越高就越富有，但真正的财富来自不依赖出售时间的资产。"
            * 8
        )
        rewritten = (
            "高工资并不等于自由，能在休息时继续创造价值的系统，才会让生活摆脱工时束缚。"
            * 8
        )
        source_size = len(translate._normalize_for_comparison(source_text))
        rewritten_size = len(translate._normalize_for_comparison(rewritten))
        self.assertGreater(rewritten_size / source_size, 0.95)
        self.assertLessEqual(rewritten_size / source_size, 1.10)
        post.side_effect = [
            _FakeResponse({
                "article_en": (
                    "A high income is often mistaken for wealth, but genuine wealth "
                    "comes from assets that create value without requiring every hour "
                    "of a person's time."
                ),
                "ad_removed": False,
                "ad_notes": "",
            }),
            _FakeResponse({
                "article_zh": rewritten,
                "ad_removed": False,
                "ad_notes": "",
            }),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            result = translate.translate(
                [{"start": 0.0, "end": 20.0, "text": source_text}],
                Path(tmp),
                engine="deepseek",
                rewrite_level="high",
                source_language="zh",
                max_zh_segment_chars=21,
            )

        self.assertEqual(post.call_count, 2)
        self.assertEqual("".join(item["zh"] for item in result), rewritten)

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_chinese_roundtrip_reuses_english_bridge_after_rewrite_failure(self, post):
        english_bridge = (
            "People often wait for outside permission before changing. "
            "A genuine shift starts when that waiting ends."
        )
        almost_same = {
            "article_zh": (
                "很多人一直在等待别人允许自己改变。"
                "其实真正的改变，就是从停止等待开始。"
            ),
            "ad_removed": False,
            "ad_notes": "",
        }
        post.side_effect = [
            _FakeResponse({
                "article_en": english_bridge,
                "ad_removed": False,
                "ad_notes": "",
            }),
            _FakeResponse(almost_same),
            _FakeResponse(almost_same),
            _FakeResponse(almost_same),
        ]
        source = [
            {"start": 0.0, "end": 2.0, "text": "很多人一直在等别人允许自己改变。"},
            {"start": 2.0, "end": 4.0, "text": "其实真正的改变从停止等待开始。"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "回译连续三次"):
                translate.translate(
                    source,
                    work_dir,
                    engine="deepseek",
                    rewrite_level="high",
                    source_language="zh",
                    max_zh_segment_chars=21,
                )
            self.assertTrue((work_dir / "translation.bridge.json").is_file())

            post.reset_mock()
            post.side_effect = [
                _FakeResponse({
                    "article_zh": "把选择权收回自己手里，人生才可能真正转向。",
                    "ad_removed": False,
                    "ad_notes": "",
                }),
            ]
            result = translate.translate(
                source,
                work_dir,
                engine="deepseek",
                rewrite_level="high",
                source_language="zh",
                max_zh_segment_chars=21,
            )

        self.assertEqual(post.call_count, 1)
        retry_payload = json.loads(
            post.call_args.kwargs["json"]["messages"][1]["content"]
        )
        self.assertEqual(
            retry_payload["task"],
            "rewrite_chinese_article_from_english_bridge",
        )
        self.assertEqual(retry_payload["source_article_en"], english_bridge)
        self.assertEqual(
            "".join(item["zh"] for item in result),
            "把选择权收回自己手里，人生才可能真正转向。",
        )

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_chinese_rewrite_retries_when_result_is_too_similar_to_source(self, post):
        english_bridge = (
            "Many people wait for permission from others before changing. "
            "Change begins once that waiting ends."
        )
        post.side_effect = [
            _FakeResponse({
                "article_en": english_bridge,
                "ad_removed": False,
                "ad_notes": "",
            }),
            _FakeResponse({
                "article_zh": (
                    "很多人一直在等待别人允许自己改变。"
                    "其实真正的改变，就是从停止等待开始。"
                ),
                "ad_removed": False,
                "ad_notes": "",
            }),
            _FakeResponse({
                "article_zh": (
                    "总把决定权交给别人，人生就很难向前。"
                    "真正的转变，始于你不再等待许可。"
                ),
                "ad_removed": False,
                "ad_notes": "",
            }),
        ]
        source = [
            {"start": 0.0, "end": 2.0, "text": "很多人一直在等别人允许自己改变。"},
            {"start": 2.0, "end": 4.0, "text": "其实真正的改变从停止等待开始。"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            result = translate.translate(
                source,
                Path(tmp),
                engine="deepseek",
                rewrite_level="high",
                source_language="zh",
                max_zh_segment_chars=21,
            )

        first_request = post.call_args_list[1].kwargs["json"]
        retry_request = post.call_args_list[2].kwargs["json"]
        retry_payload = json.loads(retry_request["messages"][1]["content"])
        retry_system_prompt = retry_request["messages"][0]["content"]
        self.assertEqual(post.call_count, 3)
        self.assertIn("未通过质检", retry_payload["quality_retry"])
        self.assertEqual(
            retry_payload["retry_mode"],
            "forced_structural_rewrite",
        )
        self.assertEqual(retry_payload["source_article_en"], english_bridge)
        self.assertNotIn("source_article_zh", retry_payload)
        self.assertIn("不要尝试复原中文原句", retry_system_prompt)
        self.assertGreater(
            retry_request["temperature"],
            first_request["temperature"],
        )
        self.assertEqual(
            "".join(item["zh"] for item in result),
            "总把决定权交给别人，人生就很难向前。真正的转变，始于你不再等待许可。",
        )

    @patch.object(translate.config, "DEEPSEEK_API_KEY", "test-key")
    @patch.object(translate.requests, "post")
    def test_chinese_deep_rewrite_uses_a_third_progressive_strategy(self, post):
        english_bridge = (
            "People often hand control of change to outside approval. "
            "A real shift begins only when the waiting stops."
        )
        almost_same = {
            "article_zh": (
                "很多人一直在等待别人允许自己改变。"
                "其实真正的改变，就是从停止等待开始。"
            ),
            "ad_removed": False,
            "ad_notes": "",
        }
        post.side_effect = [
            _FakeResponse({
                "article_en": english_bridge,
                "ad_removed": False,
                "ad_notes": "",
            }),
            _FakeResponse(almost_same),
            _FakeResponse(almost_same),
            _FakeResponse({
                "article_zh": (
                    "人生迟迟没有转向，常常是因为你把决定权交了出去。"
                    "不再等待外界许可，改变才真正发生。"
                ),
                "ad_removed": False,
                "ad_notes": "",
            }),
        ]
        source = [
            {"start": 0.0, "end": 2.0, "text": "很多人一直在等别人允许自己改变。"},
            {"start": 2.0, "end": 4.0, "text": "其实真正的改变从停止等待开始。"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            result = translate.translate(
                source,
                Path(tmp),
                engine="deepseek",
                rewrite_level="high",
                source_language="zh",
                max_zh_segment_chars=21,
            )

        third_request = post.call_args_list[3].kwargs["json"]
        third_payload = json.loads(third_request["messages"][1]["content"])
        self.assertEqual(post.call_count, 4)
        self.assertEqual(
            third_payload["retry_mode"],
            "new_narrative_angle",
        )
        self.assertEqual(third_payload["source_article_en"], english_bridge)
        self.assertNotIn("source_article_zh", third_payload)
        self.assertIn(
            "当前仍只能依据英文语义稿重新创作",
            third_request["messages"][0]["content"],
        )
        self.assertEqual(
            "".join(item["zh"] for item in result),
            "人生迟迟没有转向，常常是因为你把决定权交了出去。不再等待外界许可，改变才真正发生。",
        )


if __name__ == "__main__":
    unittest.main()
