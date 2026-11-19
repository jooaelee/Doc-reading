# -*- coding: utf-8 -*-
"""검증 규칙의 잠금.

규칙이 이 도구의 자산이다. 양식이 몇 종이든 안 흔들리기 때문이다. 수량 곱하기 단가가
금액이라는 것은 중국 대행사가 엑셀로 만든 인보이스에서도 SAP 이 찍은 인보이스에서도
같다. 그래서 양식을 세는 대신 규칙을 센다.

각 규칙은 틀린 값을 실제로 잡아야 하고, 맞는 값을 틀렸다고 하면 안 된다. 둘 다 본다.
(j) 는 정산팀 로그에서 실제로 본 잘못을 옮긴 것이다. 같은 서류를 세 번 돌렸더니
대행사 IATA 코드와 계정 번호가 회차마다 자리를 바꿨다.
"""
import importlib.util
import json
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


class FakePage:
    kind = "TEXT"
    # 원문 대조(a)가 대행사 코드도 보게 됐다. 규칙 (j) 를 재는 값은 원문에 있어야 한다.
    text = "AGENT'S IATA CODE 3430409/0004 ACCOUNT NO. TPE1464138"


def run(ex):
    """실제 차례대로 돌린다. postprocess 가 dims_cm 을 [[L,W,H,n], ...] 로 바꾸므로
    그 단계를 건너뛰면 verify 가 받는 것과 다른 모양으로 시험하게 된다."""
    return {(k, grade) for grade, k, _ in dp.verify(dp.postprocess(dict(ex)), [FakePage()], {})}


def graded(ex, key):
    """같은 키에 규칙이 둘 걸리면(원문 대조와 형식) 나쁜 쪽을 돌려준다."""
    rank = {"FAIL": 2, "WARN": 1, "OK": 0}
    got = [g for g, k, _ in dp.verify(dp.postprocess(dict(ex)), [FakePage()], {}) if k == key]
    return max(got, key=rank.get) if got else None


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 새_규칙(unittest.TestCase):
    def test_j_IATA_코드_자리에_계정_번호가_오면_잡는다(self):
        # 실제로 본 잘못. 회차마다 둘이 자리를 바꿨다.
        self.assertEqual(graded({"agent_iata_code": "TPE1464138",
                                 "agent_account_no": "3430409/0004"}, "agent_iata_code"), "FAIL")
        self.assertEqual(graded({"agent_iata_code": "3430409/0004",
                                 "agent_account_no": "TPE1464138"}, "agent_iata_code"), "OK")

    def test_j_CASS_꼴도_받는다(self):
        # 롯데 AWB 는 17-3 0413/001 2 처럼 찍는다. 공백과 하이픈을 빼면 일곱 자리다.
        FakePage.text += " 17-3 0413/001 2"
        self.assertEqual(graded({"agent_iata_code": "17-3 0413/001 2"}, "agent_iata_code"), "OK")

    def test_j_둘이_같은_값이면_잡는다(self):
        self.assertEqual(graded({"agent_iata_code": "3430409", "agent_account_no": "3430409"},
                                "agent_code_vs_account"), "FAIL")

    def test_k_요금_칸의_합이_합계와_달라지면_잡는다(self):
        good = {"awb_weight_charge": 100.0, "awb_tax": 10.0, "awb_other_charges_agent": 5.0,
                "awb_total": 115.0}
        self.assertEqual(graded(good, "awb_charges"), "OK")
        bad = {**good, "awb_total": 200.0}
        self.assertEqual(graded(bad, "awb_charges"), "FAIL")

    def test_k_칸이_하나뿐이면_판단하지_않는다(self):
        self.assertIsNone(graded({"awb_weight_charge": 100.0, "awb_total": 115.0}, "awb_charges"))

    def test_l_순중량이_총중량을_넘으면_잡는다(self):
        self.assertEqual(graded({"pl_net": 120.0, "pl_gross": 100.0}, "pl_net_le_gross"), "FAIL")
        self.assertEqual(graded({"pl_net": 90.0, "pl_gross": 100.0}, "pl_net_le_gross"), "OK")
        # 둘이 같은 것은 흔하다. 틀렸다고 하면 안 된다.
        self.assertEqual(graded({"pl_net": 100.0, "pl_gross": 100.0}, "pl_net_le_gross"), "OK")

    def test_m_포장명세서_줄_중량_합이_총계와_달라지면_잡는다(self):
        lines = [{"description": "a", "qty": 1, "net": 10.0, "gross": 12.0},
                 {"description": "b", "qty": 1, "net": 20.0, "gross": 24.0}]
        self.assertEqual(graded({"pl_lines": lines, "pl_net": 30.0}, "pl_line_net"), "OK")
        # 줄 중량은 KEROL 로 안 가고 낱개 중량을 적는 양식이 많아 배치 397건 중 85건이 걸렸다. 경고까지만 한다.
        self.assertEqual(graded({"pl_lines": lines, "pl_net": 35.0}, "pl_line_net"), "WARN")
        self.assertEqual(graded({"pl_lines": lines, "pl_gross": 36.0}, "pl_line_gross"), "OK")

    def test_n_용적이_치수에서_나온_값과_달라지면_짚는다(self):
        dims = [{"l": 100.0, "w": 100.0, "h": 100.0, "n": 2.0}]   # 2 CBM
        self.assertEqual(graded({"dims_cm": dims, "measurement_cbm": 2.0}, "measurement_cbm"), "OK")
        self.assertEqual(graded({"dims_cm": dims, "measurement_cbm": 5.0}, "measurement_cbm"), "WARN")

    def test_n_치수에_글자가_섞여도_검증이_죽지_않는다(self):
        # 실제 서류 점검(11/6)에서 모델이 치수를 "120 x 80" 같은 글자로 넣어 verify 의 곱셈이
        # TypeError 로 죽고 묶음 전체가 「오류」로 끝났다. 숫자인 치수만 쓰고 나머지는 버린다.
        dims = [{"l": "100", "w": "100", "h": "100", "n": "2"},     # 글자로 온 숫자는 숫자로 본다
                {"l": "120 x 80", "w": "N/A", "h": None, "n": 1}]    # 숫자가 안 되는 치수는 버린다
        self.assertEqual(graded({"dims_cm": dims, "measurement_cbm": 2.0}, "measurement_cbm"), "OK")
        self.assertEqual(graded({"dims_cm": dims, "awb_chargeable": 333.5, "awb_gross": 10.0}, "chargeable"), "OK")
        # postprocess 를 거치지 않은 목록 꼴에 글자가 있어도 같다
        raw = {"dims_cm": [["100", "100", "100", "2"], ["abc", 1, 1, 1]], "measurement_cbm": 2.0}
        got = [g for g, k, _ in dp.verify(raw, [FakePage()], {}) if k == "measurement_cbm"]
        self.assertEqual(got, ["OK"])

    def test_o_인코텀즈와_운임_조건이_어긋나면_짚는다(self):
        self.assertEqual(graded({"incoterms": "FOB", "freight_term": "COLLECT"},
                                "incoterms_vs_freight"), "OK")
        self.assertEqual(graded({"incoterms": "CIF", "freight_term": "PREPAID"},
                                "incoterms_vs_freight"), "OK")
        # 예외가 있는 거래라 FAIL 이 아니라 WARN 이다.
        self.assertEqual(graded({"incoterms": "EXW", "freight_term": "PREPAID"},
                                "incoterms_vs_freight"), "WARN")

    def test_o_모르는_인코텀즈는_판단하지_않는다(self):
        self.assertIsNone(graded({"incoterms": "ZZZ", "freight_term": "PREPAID"},
                                 "incoterms_vs_freight"))

    def test_p_하우스와_마스터가_같은_번호면_잡는다(self):
        # 후처리가 하우스 자리의 마스터 꼴(3자리 + 구분 기호 + 8자리)은 먼저 옮겨 버린다. 그 꼴이 아닌
        # 같은 번호는 규칙이 잡는다.
        self.assertEqual(graded({"hawb": "98813444266", "mawb": "988-13444266"},
                                "hawb_vs_mawb"), "FAIL")
        ex = dp.postprocess({"hawb": "988-13444266", "mawb": "988-13444266"})
        self.assertIsNone(ex["hawb"])
        self.assertEqual(graded({"hawb": "J283048", "mawb": "988-13444266"},
                                "hawb_vs_mawb"), "OK")

    def test_값이_없으면_아무_말도_하지_않는다(self):
        """빈 서류에 대고 규칙이 소리를 내면 사람이 검증을 꺼 버린다."""
        self.assertEqual(run({}), set())


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 프로필(unittest.TestCase):
    def tearDown(self):
        dp.set_prompt("v3")

    def test_v3_는_건드리지_않았다(self):
        """04 실측은 v2 로, 그 뒤 실측은 v3 로 쟀다. v4 를 넣느라 이 둘이 바뀌면
        앞선 숫자와 견줄 수 없게 된다."""
        dp.set_prompt("v3")
        self.assertEqual(len(dp.SCHEMA["properties"]), 31)
        dp.set_prompt("v2")
        self.assertEqual(len(dp.SCHEMA["properties"]), 27)

    def test_v4_는_v3_를_전부_담는다(self):
        self.assertTrue(set(dp.SCHEMA_V3["properties"]) <= set(dp.SCHEMA_V4["properties"]))
        self.assertTrue(set(dp.SCHEMA_V3["required"]) <= set(dp.SCHEMA_V4["required"]))

    def test_v4_가_더_뽑는_칸은_전부_규칙이_쓴다(self):
        """뽑기만 하고 아무도 안 보는 칸은 프롬프트만 길어지고 기존 칸을 갉는다."""
        extra = set(dp.SCHEMA_V4["properties"]) - set(dp.SCHEMA_V3["properties"])
        src = open(os.path.join(ROOT, "tools", "docpipe.py"), encoding="utf-8").read()
        body = src[src.index("def verify("):]
        unused = sorted(k for k in extra if f'"{k}"' not in body)
        self.assertEqual(unused, [], f"규칙이 안 쓰는 칸: {unused}")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class CLI_경로(unittest.TestCase):
    """API 키 없이 Claude Code CLI 로 부르는 길.

    앞선 판에서 이 길이 스키마를 강제 못 하고 스캔본도 못 읽는다고 적었는데 둘 다
    틀렸다. 스키마는 MCP 도구로 박고, 스캔본은 CLI 의 Read 가 그림으로 읽는다.
    _json_from_text 는 모델이 도구를 안 부르고 글로 답했을 때만 쓰는 뒷길이다.
    """

    def test_울타리와_군말을_벗긴다(self):
        for text in ('{"a":1}',
                     '```json\n{"a":1}\n```',
                     '```\n{"a":1}\n```',
                     'Here is the JSON:\n{"a":1}\nLet me know if you need more.'):
            self.assertEqual(dp._json_from_text(text), {"a": 1}, text)

    def test_문자열_안의_중괄호를_세지_않는다(self):
        self.assertEqual(dp._json_from_text('{"a":"has { brace"}'), {"a": "has { brace"})
        self.assertEqual(dp._json_from_text(r'{"a":"quote \" and { "}'), {"a": 'quote " and { '})

    def test_중첩된_객체를_끝까지_읽는다(self):
        self.assertEqual(dp._json_from_text('말\n{"a":{"b":{"c":1}}}\n끝'), {"a": {"b": {"c": 1}}})

    def test_JSON_이_없으면_말해_준다(self):
        with self.assertRaises(ValueError):
            dp._json_from_text("죄송합니다. 문서를 읽을 수 없습니다.")
        with self.assertRaises(ValueError):
            dp._json_from_text('{"a":1')


class MCP_서버(unittest.TestCase):
    """스키마를 박는 자리. 모델을 부르지 않고 규약만 본다."""

    def _talk(self, msgs, schema):
        import json as J, subprocess, tempfile, os as O
        work = tempfile.mkdtemp()
        sc, out = O.path.join(work, "s.json"), O.path.join(work, "o.json")
        with open(sc, "w", encoding="utf-8") as f:
            J.dump(schema, f)
        env = dict(O.environ, EXTRACT_SCHEMA=sc, EXTRACT_OUT=out)
        server = O.path.join(ROOT, "tools", "extract_mcp.py")
        p = subprocess.run([sys.executable, server], input="\n".join(J.dumps(m) for m in msgs),
                           capture_output=True, text=True, env=env, timeout=30)
        replies = [J.loads(x) for x in p.stdout.splitlines() if x.strip()]
        saved = J.load(open(out, encoding="utf-8")) if O.path.exists(out) else None
        return replies, saved

    def test_규약대로_답한다(self):
        schema = {"type": "object", "properties": {"hawb": {"type": "string"}}}
        replies, _ = self._talk([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ], schema)
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "extract")
        tools = replies[1]["result"]["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "extract")
        self.assertEqual(tools[0]["inputSchema"], schema, "스키마를 그대로 도구에 박아야 한다")

    def test_부른_인자를_적는다(self):
        _, saved = self._talk([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "extract", "arguments": {"hawb": "J283048", "awb_gross": 10}}},
        ], {"type": "object"})
        self.assertEqual(saved, {"hawb": "J283048", "awb_gross": 10})

    @unittest.skipUnless(HAVE, "pymupdf 가 없다")
    def test_실제_스키마를_그대로_싣는다(self):
        dp.set_prompt("v4")
        schema = dp.SCHEMA_STRICT
        dp.set_prompt("v3")
        replies, _ = self._talk([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ], schema)
        got = replies[1]["result"]["tools"][0]["inputSchema"]
        self.assertEqual(len(got["properties"]), 41)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE, "docpipe 가 안 올라옵니다")
class 경로누출(unittest.TestCase):
    """프롬프트에 케이스 번호와 고객 이름이 실려 가지 않는지 본다.

    실측에서 한 번 당했다. 케이스 폴더 이름이 HAWB 번호라서 모델이 서류를 안 읽고
    경로에서 번호를 가져왔고, 값은 맞았는데 출처가 틀렸다. 실제 운영에서는 폴더
    이름이 그렇게 붙지 않으므로 그대로 두면 측정이 거짓말을 한다.
    """

    def test_뜻_없는_이름으로_베낀다(self):
        import shutil, tempfile
        src = tempfile.mkdtemp(prefix="leak-")
        case = os.path.join(src, "SMP10000001")
        os.makedirs(case)
        a = os.path.join(case, "ACME무역_BL12345.pdf")
        open(a, "wb").write(b"%PDF-1.4\n")
        dest = os.path.join(tempfile.mkdtemp(prefix="leak-out-"), "docs")
        got = dp.neutral_copies([a], dest)
        self.assertEqual(len(got), 1)
        joined = " ".join(got)
        self.assertNotIn("SMP10000001", joined)
        self.assertNotIn("ACME", joined)
        self.assertNotIn("BL12345", joined)
        self.assertTrue(got[0].endswith("doc01.pdf"))
        self.assertTrue(os.path.exists(got[0]))
        shutil.rmtree(src, ignore_errors=True)

    def test_확장자는_지킨다(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="leak-")
        f = os.path.join(d, "스캔본.PDF")
        open(f, "wb").write(b"%PDF-1.4\n")
        got = dp.neutral_copies([f], os.path.join(d, "out"))
        self.assertTrue(got[0].endswith(".pdf"))


@unittest.skipUnless(HAVE, "docpipe 가 안 올라옵니다")
class 덩어리청구(unittest.TestCase):
    """인보이스가 한 덩어리로 청구하면 포장 명세서의 낱개 수와 안 맞는 것이 정상이다."""

    def 재다(self, ci_qty, pl_qty):
        ex = {"ci_lines": [{"description": "PARTS", "qty": ci_qty, "unit_price": 8e6, "amount": 8e6 * ci_qty}],
              "pl_lines": [{"description": "PARTS", "qty": pl_qty}], "ci_total": 8e6 * ci_qty}
        res = dp.verify(ex, [FakePage()], {})
        return [(g, m) for g, k, m in res if k == "pl_vs_ci_qty"]

    def test_수량_1_은_경고까지만(self):
        got = self.재다(1, 200)
        self.assertEqual(got[0][0], "WARN")
        self.assertIn("한 덩어리", got[0][1])

    def test_같으면_통과(self):
        self.assertEqual(self.재다(200, 200)[0][0], "OK")

    def test_둘_다_낱개인데_다르면_경고한다(self):
        self.assertEqual(self.재다(150, 200)[0][0], "WARN")


@unittest.skipUnless(HAVE, "docpipe 가 안 올라옵니다")
class 쪽머리글(unittest.TestCase):
    """프롬프트의 쪽 머리글에 파일 이름이 안 들어가는지 본다.

    파일 이름에는 케이스 번호와 고객 이름이 흔히 붙는다. CLI 쪽 경로를 막았더니
    같은 구멍이 API 쪽 프롬프트에도 있었다. 한 번에 둘 다 막고 여기서 잠근다.
    """

    class P:
        def __init__(self, file, no, kind, text):
            self.file, self.no, self.kind, self.text = file, no, kind, text
        images = 0

    def test_파일_이름을_안_쓴다(self):
        pages = [self.P("/x/SMP10000001/ACME무역_BL1.pdf", 1, "AWB", "AIR WAYBILL"),
                 self.P("/x/SMP10000001/ACME무역_BL1.pdf", 2, "CI", "INVOICE"),
                 self.P("/x/SMP10000001/고객사_BL2.pdf", 1, "PL", "PACKING LIST")]
        body = dp.build_messages(pages)[1]["content"]
        for bad in ("SMP10000001", "ACME", "고객사", "BL1", "BL2", ".pdf"):
            self.assertNotIn(bad, body)

    def test_쪽과_종류는_남긴다(self):
        pages = [self.P("/x/a.pdf", 1, "AWB", "AIR WAYBILL"),
                 self.P("/x/b.pdf", 3, "PL", "PACKING LIST")]
        body = dp.build_messages(pages)[1]["content"]
        self.assertIn("page 1 of doc1 (AWB)", body)
        self.assertIn("page 3 of doc2 (PL)", body)
        self.assertIn("AIR WAYBILL", body)

    def test_같은_파일은_같은_번호를_받는다(self):
        pages = [self.P("/x/a.pdf", 1, "AWB", "one"), self.P("/x/a.pdf", 2, "CI", "two")]
        body = dp.build_messages(pages)[1]["content"]
        self.assertEqual(body.count("doc1"), 2)
        self.assertNotIn("doc2", body)


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 판v5(unittest.TestCase):
    def test_v5_는_v4_와_칸이_같다(self):
        self.assertEqual(dp.PROFILES["v5"]()[0], dp.PROFILES["v4"]()[0])

    def test_v5_는_v4_규칙을_다_담고_자리를_못_박는다(self):
        s4, s5 = dp.PROFILES["v4"]()[2], dp.PROFILES["v5"]()[2]
        self.assertTrue(s5.startswith(s4.replace("\nReturn only the JSON object.", "")))
        for w in ("hawb:", "mawb:", "shipper_name", "origin / destination", "item_desc", "ci_total", "freight_term"):
            self.assertIn(w, s5.split("Where a value can be read")[1])


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 판v6(unittest.TestCase):
    """프롬프트에 보기 값이 없는지 잠근다. 모델이 칸을 못 찾으면 보기를 그대로 적었다."""

    LEAKED = ("3430409", "J283048", "2076679570", "988-13444266", "OZ752", "6582", "TAIPEI", "DALIAN")

    def test_v6_에는_보기_값이_없다(self):
        s6 = dp.PROFILES["v6"]()[2]
        for w in self.LEAKED:
            self.assertNotIn(w, s6)
        self.assertNotIn("e.g.", s6)

    def test_v6_는_v4_와_칸이_같다(self):
        self.assertEqual(dp.PROFILES["v6"]()[0], dp.PROFILES["v4"]()[0])

    def test_v6_는_모든_칸을_말한다(self):
        s6 = dp.PROFILES["v6"]()[2]
        for k in dp.PROFILES["v4"]()[0]["properties"]:
            self.assertIn(k, s6, k)


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 배치에서_배운_규칙(unittest.TestCase):
    def test_d_AWB_재계량_차이는_경고다(self):
        self.assertEqual(graded({"pl_gross": 930, "awb_gross": 938}, "gross_weight"), "WARN")
        # 운송장은 포워더가 따로 잰 값이라 크게 달라도 오류가 아니라 확인이다. 대신 포장 무게를
        # 확인하라고 안내한다(docpipe.verify 의 발행 주체 규칙).
        self.assertEqual(graded({"pl_gross": 31, "awb_gross": 68}, "gross_weight"), "WARN")
        # 송하인 서류끼리 다르면 FAIL
        self.assertEqual(graded({"pl_gross": 100, "ci_gross": 120, "awb_gross": 101}, "gross_weight"), "FAIL")

    def test_m_줄_중량_0_은_안_센다(self):
        self.assertIsNone(graded({"pl_lines": [{"gross": 0, "qty": 1}, {"gross": 0, "qty": 1}], "pl_gross": 7}, "pl_line_gross"))

    def test_h_원문에_그대로_있는_번호는_경고다(self):
        FakePage.text += " 180-56551061"
        self.assertEqual(graded({"mawb": "180-56551061"}, "mawb_check_digit"), "WARN")

    def test_두_표를_다_담은_줄은_총액에_맞는_반쪽만_남긴다(self):
        ex = dp.postprocess({"ci_lines": [{"amount": 12361}, {"amount": 12361}, {"amount": 1977.91}, {"amount": 1977.91}],
                             "ci_total": 3955.82})
        self.assertEqual([l["amount"] for l in ex["ci_lines"]], [1977.91, 1977.91])
        ex = dp.postprocess({"ci_lines": [{"amount": 5}, {"amount": 6}], "ci_total": 11})
        self.assertEqual(len(ex["ci_lines"]), 2)


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 통화기호(unittest.TestCase):
    def test_기호로_찍힌_통화는_원문에_있는_것이다(self):
        FakePage.text += " Unit price US $ 930.00"
        self.assertEqual(graded({"currency": "USD"}, "currency"), "OK")


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 해상마스터(unittest.TestCase):
    def test_글자가_섞인_마스터_BL_은_자릿수를_안_따진다(self):
        FakePage.text += " SNKO0123456789 180-1234567"
        self.assertIsNone(graded({"mawb": "SNKO0123456789"}, "mawb_format"))
        self.assertEqual(graded({"mawb": "180-1234567"}, "mawb_format"), "WARN")


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 끊긴답살리기(unittest.TestCase):
    """서버가 목록 한가운데서 끊고 정상 종료라고 적어 보내는 일이 있다.

    솔라가 포장명세서의 같은 줄을 되풀이하다 143토큰에서 멈췄고, 같은 조건으로 세 번
    불러도 같은 자리였다. 통째로 버리면 그 케이스가 채점에서 빠져 분모가 조용히 줄고
    성적이 남은 케이스의 것으로 적힌다. 끝난 원소까지만 받는다.
    """

    def test_온전한_JSON_은_그대로_둔다(self):
        v, salvaged = dp.loads_partial('{"a": 1, "lines": [{"q": 1}]}')
        self.assertEqual(v, {"a": 1, "lines": [{"q": 1}]})
        self.assertFalse(salvaged)

    def test_목록_한가운데서_끊기면_끝난_원소까지_받는다(self):
        v, salvaged = dp.loads_partial('{"lines": [{"q": 1}, {"q": 2}, {"q"')
        self.assertTrue(salvaged)
        self.assertEqual(v["lines"], [{"q": 1}, {"q": 2}])

    def test_문자열_안의_괄호를_구조로_세지_않는다(self):
        v, salvaged = dp.loads_partial('{"desc": "box ] and }", "lines": [{"q": 1},')
        self.assertTrue(salvaged)
        self.assertEqual(v["desc"], "box ] and }")
        self.assertEqual(v["lines"], [{"q": 1}])

    def test_살릴_원소가_없으면_올린다(self):
        with self.assertRaises(json.JSONDecodeError):
            dp.loads_partial('{"lines": [{"q"')
