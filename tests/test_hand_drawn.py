from src import hand_drawn as hd


def _segs(n, dur=5.0):
    return [{"start": i * dur, "end": i * dur + dur - .5, "zh": f"第{i}句话讲一个道理。"} for i in range(n)]


def test_split_chapters_covers_all_segments_in_order():
    segs = _segs(30)
    chapters = hd.split_chapters(segs, chapter_seconds=40)
    flat = [i for ch in chapters for i in ch]
    assert flat == list(range(30))
    assert len(chapters) >= 3


def _director(n):
    d = hd._fallback_director(n)
    d["actors"].append({"id": "kid", "name": "孩子", "look": {"age": "child"}})
    return d


def test_build_world_prefixes_ids_and_remaps_edges():
    lay = hd._layout(1920, 1080)
    stages = [hd._stage(k, lay) for k in range(2)]
    plans = [
        {"icons": [{"id": "book", "x": 0, "y": -90}], "notes": [], "nodes": [
            {"id": "a", "x": 0, "y": -600, "name": "甲"}, {"id": "b", "x": 400, "y": -600, "name": "乙"}],
         "edges": [{"id": "ab", "type": "dotted", "a": "a", "b": "b"}, {"id": "bad", "type": "dotted"}],
         "lines": []},
        {"icons": [{"id": "book", "icon": "book", "x": 6000, "y": -90}], "notes": [], "nodes": [], "lines": []},
    ]
    world, maps = hd.build_world(_director(2), plans, stages, lay)
    assert "KID" in world["ACTORS"] and "HOST" in world["ACTORS"]
    assert world["ICON_POS"]["c0_book"]["icon"] == "book"
    assert "c1_book" in world["ICON_POS"]
    assert world["EDGES"]["c0_ab"]["a"] == "c0_a"
    assert "c0_bad" not in world["EDGES"]
    assert maps[0]["stage"] == "c0" and "c0" in world["AREAS"]


def test_clean_action_validates_and_rewrites():
    lay = hd._layout(1920, 1080)
    stages = [hd._stage(0, lay)]
    plans = [{"icons": [{"id": "bulb", "x": 0, "y": -500}], "notes": [], "nodes": [], "lines": []}]
    world, maps = hd.build_world(_director(1), plans, stages, lay)
    m = maps[0]
    assert hd._clean_action("icon bulb", m, world, "c0") == "icon c0_bulb"
    assert hd._clean_action("icon nothing", m, world, "c0") is None
    assert hd._clean_action("pose NOBODY cheer", m, world, "c0") is None
    assert hd._clean_action("wind", m, world, "c0") is None
    assert hd._clean_action("camz 1.3 HOST bulb", m, world, "c0") == "camz 1.3 HOST c0_bulb"
    assert hd._clean_action("cam stage", m, world, "c0") == "cam c0"
    assert hd._clean_action("say HOST 你好 世界", m, world, "c0") == "say HOST 你好 世界"
    assert hd._clean_action("walk HOST 120.7", m, world, "c0") == "walk HOST 121"


def test_build_timeline_places_and_shows_actors_and_times_keywords():
    segs = _segs(4)
    chapters = [[0, 1], [2, 3]]
    lay = hd._layout(1920, 1080)
    stages = [hd._stage(k, lay) for k in range(2)]
    plans = [
        {"icons": [], "notes": [], "nodes": [], "lines": [
            {"i": 0, "cues": ["^ > place HOST 100; actor HOST", "道理 > pose HOST point; talk HOST"]},
            {"i": 1, "cues": ["不存在的词 > emote HOST !"]}]},
        {"icons": [], "notes": [], "nodes": [], "lines": [{"i": 2, "cues": ["^ > walk KID 6100"]}]},
    ]
    director = _director(2)
    world, maps = hd.build_world(director, plans, stages, lay)
    tl = hd.build_timeline(segs, chapters, plans, director, world, maps, stages, duration=22,
                           engine_subs=True, size=(1920, 1080), voice_env="")
    acts = [e["act"] for e in tl["events"]]
    assert "cam c0" in acts and "cam c1" in acts
    # KID 在第 2 章没有 place / actor 就开始走：系统先把他挪到本章舞台并让他上场
    assert any(a.startswith("place KID ") for a in acts)
    assert "actor KID" in acts
    talk = next(e for e in tl["events"] if e["act"].startswith("talk HOST"))
    assert talk["act"] == f"talk HOST {segs[0]['end']:.3f}"
    point = next(e for e in tl["events"] if e["act"] == "pose HOST point")
    assert segs[0]["start"] < point["t"] < segs[0]["end"]
    assert tl["subs"] and all(s["t1"] > s["t0"] for s in tl["subs"])
