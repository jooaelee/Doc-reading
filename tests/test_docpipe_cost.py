# -*- coding: utf-8 -*-
"""값을 재는 자리의 잠금.

비용을 한 번도 못 쟀던 이유가 둘이다. 하나는 단가표에 sonnet-4.6 의 값이 적혀 있었던
것이고, 다른 하나는 나눠 부른 뒤 합치는 자리에서 cli_cost_usd 와 캐시 두 칸을 안 더한
것이다. 둘 다 조용히 틀린 값을 내므로 표를 보고는 알 수가 없다. 그래서 잠근다.

나눠 부르기를 API 로도 부를 수 있어야 한다. 그 얼개가 정답 대조를 78.5% 에서 98.1% 로
올렸는데 CLI 에만 걸려 있었다. 운영에서 쓸 경로는 API 다.
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("docpipe", os.path.join(ROOT, "tools", "docpipe.py"))
dp = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(dp)
    HAVE = True
except Exception:
    HAVE = False


@unittest.skipUnless(HAVE, "docpipe 를 못 읽었습니다")
class 단가표(unittest.TestCase):
    def test_sonnet_5_는_2달러와_10달러다(self):
        # 3/15 는 sonnet-4.6 의 값이다. 이 값이 박혀 있던 동안 usd_sonnet 열이 1.5배였다.
        self.assertEqual(dp.PRICE["sonnet-5"], (2.0, 10.0))

    def test_모든_단가는_출력이_입력보다_비싸다(self):
        for name, (pi, po) in dp.PRICE.items():
            self.assertGreater(po, pi, name)


@unittest.skipUnless(HAVE, "docpipe 를 못 읽었습니다")
class 환산비용(unittest.TestCase):
    def test_캐시_쓰기는_1_25배이고_읽기는_0_1배다(self):
        usd = dp.api_cost("claude-sonnet-5", dict(
            input_tokens=1_000_000, cache_creation_input_tokens=1_000_000,
            cache_read_input_tokens=1_000_000, output_tokens=1_000_000))
        self.assertAlmostEqual(usd, 2.0 + 2.5 + 0.2 + 10.0, places=6)

    def test_모르는_모델이면_값을_안_낸다(self):
        self.assertIsNone(dp.api_cost("gpt-9", dict(input_tokens=100)))

    def test_빈_usage_는_0이다(self):
        self.assertEqual(dp.api_cost("claude-opus-5", {}), 0.0)


@unittest.skipUnless(HAVE, "docpipe 를 못 읽었습니다")
class 끊긴_호출의_환산(unittest.TestCase):
    """결과 사건이 오기 전에 끊기면 토큰만 세고 실비는 안 낸다.

    out.json 이 생기면 CLI 를 바로 끊기 때문에 이 갈래가 보통 길이다. 조용히 틀리면
    케이스당 실비가 통째로 틀리므로 잠근다."""

    def 사건(self):
        return [{"type": "assistant", "message": {"usage": {
            "input_tokens": 1000, "output_tokens": 1000,
            "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 1000}}}]

    def test_결과_사건이_있으면_CLI_가_적은_값을_쓴다(self):
        ev = self.사건() + [{"type": "result", "total_cost_usd": 0.1234, "num_turns": 3,
                             "usage": {"input_tokens": 7, "output_tokens": 9}}]
        u = dp._usage_from_events(ev, "claude-opus-5")
        self.assertEqual(u["cli_cost_usd"], 0.1234)
        self.assertEqual(u["prompt_tokens"], 7)

    def test_끊기면_실비를_안_낸다(self):
        """우리가 곱해 봤더니 같은 케이스에서 $0.5072 가 나왔고 잰 값은 $0.2259 였다.
        캐시 읽기로 갈 몫이 입력 자리에 잡히기 때문이다. 틀린 숫자 대신 빈 값을 낸다."""
        for m in ("claude-sonnet-5", "claude-opus-5", None):
            self.assertIsNone(dp._usage_from_events(self.사건(), m)["cli_cost_usd"])

    def test_토큰은_그대로_센다(self):
        u = dp._usage_from_events(self.사건(), "claude-sonnet-5")
        self.assertEqual(u["prompt_tokens"], 1000)
        self.assertEqual(u["cache_read"], 1000)
        self.assertEqual(u["cache_write"], 1000)

    def test_사건이_없으면_비어_있다(self):
        u = dp._usage_from_events([], "claude-sonnet-5")
        self.assertIsNone(u["cli_cost_usd"])
        self.assertEqual(u["turns"], 0)

    def test_같은_message_는_한_번만_센다(self):
        """실측: 같은 message.id 가 두 번씩 온다. 그대로 더하면 입력과 캐시가 두 배가 된다."""
        def 조각(mid, cr):
            return {"type": "assistant", "message": {"id": mid, "usage": {
                "input_tokens": 2, "output_tokens": 8,
                "cache_read_input_tokens": cr, "cache_creation_input_tokens": 100}}}
        ev = [조각("m1", 31631), 조각("m1", 31631), 조각("m2", 39801), 조각("m2", 39801)]
        u = dp._usage_from_events(ev, "claude-sonnet-5")
        self.assertEqual(u["turns"], 2)
        self.assertEqual(u["prompt_tokens"], 4)
        self.assertEqual(u["cache_read"], 31631 + 39801)
        self.assertEqual(u["cache_write"], 200)

    def test_끊긴_호출은_잰_값이_아니라고_표시한다(self):
        self.assertTrue(dp._usage_from_events(self.사건(), "claude-sonnet-5")["usage_partial"])
        ev = self.사건() + [{"type": "result", "total_cost_usd": 0.1, "usage": {}}]
        self.assertNotIn("usage_partial", dp._usage_from_events(ev, "claude-sonnet-5"))


@unittest.skipUnless(HAVE, "docpipe 를 못 읽었습니다")
class 출력_토큰_채우기(unittest.TestCase):
    """스트림의 output_tokens 는 쓰는 중인 값이라 8 이나 2 로 나온다. 받아 낸 JSON 으로 채운다."""

    def 부분(self):
        return dict(prompt_tokens=4, completion_tokens=10, cache_read=1000, cache_write=0,
                    usage_partial=True, cli_cost_usd=None)

    def test_JSON_이_크면_그_길이로_바꾼다(self):
        raw = {"item_desc": "VALVE THROTTLE 3 inch " * 40}
        u = dp._fill_partial_usage(self.부분(), raw, "claude-sonnet-5")
        self.assertGreater(u["completion_tokens"], 100)
        self.assertTrue(u["usage_partial"])   # 채운 값이지 잰 값이 아니다
        self.assertIsNone(u["cli_cost_usd"])  # 채웠다고 실비를 내지는 않는다

    def test_잰_값이면_손대지_않는다(self):
        잰것 = dict(completion_tokens=1331, cli_cost_usd=0.06)
        self.assertEqual(dp._fill_partial_usage(잰것, {"a": "b" * 500}, "claude-sonnet-5"), 잰것)

    def test_JSON_이_아니면_손대지_않는다(self):
        b = self.부분()
        self.assertEqual(dp._fill_partial_usage(b, None, "claude-sonnet-5"), b)

    def test_채운_값이_이미_센_값보다_작으면_안_내린다(self):
        u = dict(self.부분(), completion_tokens=900)
        self.assertEqual(dp._fill_partial_usage(u, {"a": 1}, "claude-sonnet-5")["completion_tokens"], 900)


class 가짜쪽:
    def __init__(self, kind, text, no=1):
        self.kind, self.text, self.no = kind, text, no
        self.file, self.image, self.dropped = "a.pdf", None, False

    @property
    def needs_image(self):
        return False


@unittest.skipUnless(HAVE, "docpipe 를 못 읽었습니다")
class 나눠_부른_값_합치기(unittest.TestCase):
    def 부르기(self, caller, 값들):
        pages = [가짜쪽("CI", "COMMERCIAL INVOICE\nINVOICE NO 703952045\nTOTAL 10.00", 1),
                 가짜쪽("PL", "PACKING LIST\nCTN 6\nNET 4.52", 2)]
        본 = []

        def 가짜(model, msgs, schema=None, **kw):
            본.append(schema)
            return {}, 값들[len(본) - 1]

        old_cli, old_api = dp.call_claude_cli, dp.call_anthropic
        dp.call_claude_cli = lambda model, msgs, paths=None, schema=None, **kw: 가짜(model, msgs, schema)
        dp.call_anthropic = lambda model, msgs, schema=None, **kw: 가짜(model, msgs, schema)
        try:
            _, stat = dp.call_split("claude-sonnet-5", pages, ["a.pdf"], {}, caller=caller)
        finally:
            dp.call_claude_cli, dp.call_anthropic = old_cli, old_api
        return stat, 본

    def test_CLI_실비와_캐시_두_칸을_다_더한다(self):
        stat, _ = self.부르기("cli", [
            dict(wall=1000, prompt_tokens=10, completion_tokens=100, cache_read=30, cache_write=5,
                 cli_cost_usd=0.11, used_tool=True),
            dict(wall=2000, prompt_tokens=20, completion_tokens=200, cache_read=40, cache_write=7,
                 cli_cost_usd=0.22, used_tool=True)])
        self.assertAlmostEqual(stat["cli_cost_usd"], 0.33)
        self.assertEqual(stat["cache_read"], 70)
        self.assertEqual(stat["cache_write"], 12)
        self.assertEqual(stat["prompt_tokens"], 30)
        self.assertEqual(stat["completion_tokens"], 300)
        # 벽시계는 동시에 부르므로 제일 늦은 것이고, 합은 따로 둔다.
        self.assertEqual(stat["wall"], 2000)
        self.assertEqual(stat["wall_sum"], 3000)

    def test_한_호출이라도_끊겼으면_꾸러미_실비를_안_낸다(self):
        """빠진 몫이 0 으로 들어가므로 합이 실제보다 작아진다. 합을 내지 않고 비운다."""
        stat, _ = self.부르기("cli", [
            dict(wall=1000, prompt_tokens=10, completion_tokens=100, cli_cost_usd=0.11, used_tool=True),
            dict(wall=2000, prompt_tokens=20, completion_tokens=200, cli_cost_usd=None,
                 usage_partial=True, used_tool=True)])
        self.assertIsNone(stat["cli_cost_usd"])
        self.assertTrue(stat["usage_partial"])
        self.assertEqual(stat["prompt_tokens"], 30)   # 토큰은 그대로 더한다

    def test_API_환산도_같은_자리에_더한다(self):
        stat, _ = self.부르기("anthropic", [
            dict(wall=900, prompt_tokens=3000, completion_tokens=700, usd=0.012, used_tool=True),
            dict(wall=800, prompt_tokens=2000, completion_tokens=600, usd=0.009, used_tool=True)])
        self.assertAlmostEqual(stat["usd"], 0.021)
        self.assertEqual(stat["completion_tokens"], 1300)

    def test_API_로_불러도_서류마다_제_스키마만_간다(self):
        _, 본 = self.부르기("anthropic", [
            dict(wall=1, prompt_tokens=1, completion_tokens=1, usd=0.0, used_tool=True)] * 2)
        self.assertEqual(len(본), 2)
        ci, pl = [set(x["properties"]) for x in 본]
        # 인보이스 칸은 인보이스 호출에만, 포장 칸은 포장명세서 호출에만 실린다.
        self.assertIn("ci_total", ci)
        self.assertNotIn("ci_total", pl)
        self.assertIn("pl_gross", pl)
        self.assertNotIn("pl_gross", ci)
        for x in 본:
            self.assertLess(len(x["properties"]), len(dp.SCHEMA_STRICT["properties"]))


if __name__ == "__main__":
    unittest.main()
