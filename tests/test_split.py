# -*- coding: utf-8 -*-
"""서류별로 나눠 부르고 합치는 길을 잠근다.

꾸러미를 한 번에 주면 두 인보이스의 총액을 모델이 더하다 틀렸다(28301.8 과 28271.2. 맞는
값은 28270.8). 나눠 부르면 인보이스마다 찍힌 총액을 그대로 받아 우리가 더한다. 합치는
규칙이 흔들리면 그 이점이 사라지므로 여기서 잠근다.
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


def page(kind, text, no=1):
    p = dp.Page("a.pdf", no, text, 0)
    p.kind = kind
    return p


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 합치기(unittest.TestCase):
    def test_인보이스가_둘이면_총액을_더하고_줄을_잇는다(self):
        out = dp.merge_parts([
            ("CI", {"invoice_no": "A1", "ci_total": 22654.8, "ci_lines": [{"qty": 1}], "currency": "USD"}),
            ("CI", {"invoice_no": "A2", "ci_total": 5616.0, "ci_lines": [{"qty": 2}], "currency": "USD"}),
        ])
        self.assertEqual(out["ci_total"], 28270.8)
        self.assertEqual(out["invoice_no"], "A1")
        self.assertEqual([l["qty"] for l in out["ci_lines"]], [1, 2])

    def test_AWB_가_인보이스보다_먼저다(self):
        out = dp.merge_parts([
            ("CI", {"consignee_name": "SOLD TO CO", "origin": "FROM X"}),
            ("AWB", {"consignee_name": "AWB CO", "origin": "INCHEON"}),
        ])
        self.assertEqual((out["consignee_name"], out["origin"]), ("AWB CO", "INCHEON"))

    def test_AWB_에_없으면_인보이스에서_채운다(self):
        out = dp.merge_parts([("AWB", {"consignee_name": None}), ("CI", {"consignee_name": "SOLD TO CO"})])
        self.assertEqual(out["consignee_name"], "SOLD TO CO")

    def test_포장명세서가_둘이면_포장_수와_중량을_더한다(self):
        out = dp.merge_parts([("PL", {"pl_packages": 21, "pl_gross": 3744}), ("PL", {"pl_packages": 2, "pl_gross": 265})])
        self.assertEqual((out["pl_packages"], out["pl_gross"]), (23, 4009))

    def test_하나뿐이면_더하지_않고_그대로_둔다(self):
        out = dp.merge_parts([("CI", {"ci_total": "1,148.92"})])
        self.assertEqual(out["ci_total"], "1,148.92")

    def test_스캔_결과는_빈_자리만_채운다(self):
        out = dp.merge_parts([("AWB", {"hawb": "H1", "mawb": None}), ("SCAN", {"hawb": "H9", "mawb": "M1", "ci_total": 5})])
        self.assertEqual((out["hawb"], out["mawb"], out["ci_total"]), ("H1", "M1", 5))


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 묶기(unittest.TestCase):
    def test_같은_종류가_이어지면_한_서류다(self):
        g = dp.split_groups([page("AWB", "awb"), page("CI", "COMMERCIAL INVOICE 1/2 x", 2),
                             page("CI", "2/2 more lines", 3), page("PL", "PACKING LIST TOTAL", 4)])
        self.assertEqual([(k, len(p)) for k, p in g], [("AWB", 1), ("CI", 2), ("PL", 1)])

    def test_인보이스_둘은_두_서류다(self):
        a = page("CI", "COMMERCIAL INVOICE No 8002854722 ... TOTAL 22654.80", 1)
        b = page("CI", "COMMERCIAL INVOICE No 8002854723 ... TOTAL 5616.00", 2)
        g = dp.split_groups([a, b])
        self.assertEqual([(k, len(p)) for k, p in g], [("CI", 1), ("CI", 1)])

    def test_스캔_쪽은_한_묶음이고_OTHER_는_앞_서류에_붙는다(self):
        g = dp.split_groups([page("AWB", "awb"), page("OTHER", "customs table", 2), page("SCAN", "", 3), page("SCAN", "", 4)])
        self.assertEqual([(k, len(p)) for k, p in g], [("AWB", 2), ("SCAN", 2)])

    def test_밀린_인보이스와_밀린_포장명세서는_따로_부른다(self):
        a, b = page("CI", "x", 1), page("PL", "y", 2)
        a.image = b.image = True
        g = dp.split_groups([a, b])
        self.assertEqual([(k, len(p)) for k, p in g], [("CI", 1), ("PL", 1)])

    def test_부분_스키마는_그_서류의_칸만_담는다(self):
        sc = dp.sub_schema("LCA")
        self.assertEqual(set(sc["properties"]), {"lca_lines", "lca_total_issue", "lca_grand_total_usd"})
        self.assertTrue(set(sc["required"]) <= set(sc["properties"]))

    def test_모든_칸이_어느_서류에든_속한다(self):
        covered = {k for ks in dp.SPLIT_KEYS.values() for k in ks}
        self.assertEqual(set(dp.SCHEMA_V4["properties"]) - covered, set())


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 주인(unittest.TestCase):
    def test_통화와_인코텀즈는_인보이스가_이긴다(self):
        out = dp.merge_parts([("AWB", {"currency": "KRW", "hawb": "H1"}), ("CI", {"currency": "USD", "incoterms": "FOB"})])
        self.assertEqual((out["currency"], out["hawb"], out["incoterms"]), ("USD", "H1", "FOB"))

    def test_포장_수는_포장명세서가_이긴다(self):
        out = dp.merge_parts([("CI", {"pl_packages": 188}), ("PL", {"pl_packages": 27})])
        self.assertEqual(out["pl_packages"], 27)


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 구매주문서(unittest.TestCase):
    def test_구매_주문서는_인보이스가_아니다(self):
        p = page("?", "PURCHASE ORDER\nPO No FCL4352400810\nOrder Qty 88 EA\nNet Price 144.56\nTOTAL ORDER AMOUNT 12,721.28 USD\nDelivery Terms FOB INCHON\nVendor CNUS")
        self.assertEqual(dp.classify(p).kind, "PO")
        self.assertIn("PO", dp.DROP)

    def test_품명_앞의_포장_수는_뗀다(self):
        self.assertEqual(dp.postprocess({"item_desc": "9 PACKAGE OF OTHERS_RETAINER RING"})["item_desc"], "OTHERS_RETAINER RING")
        self.assertEqual(dp.postprocess({"item_desc": "3 PKG OF RF GENERATOR"})["item_desc"], "RF GENERATOR")
        self.assertEqual(dp.postprocess({"item_desc": "GAS DETECTOR"})["item_desc"], "GAS DETECTOR")


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 정답대조(unittest.TestCase):
    def test_빈_배열과_널은_같다(self):
        rows = dict((k, ok) for k, ok, _ in dp.compare({"lca_lines": None}, {"lca_lines": []}))
        self.assertTrue(rows["lca_lines"])

    def test_회사_이름은_글자와_숫자만_견준다(self):
        rows = dict((k, ok) for k, ok, _ in dp.compare({"shipper_name": "EHWA DIAMOND IND.CO.,LTD"}, {"shipper_name": "EHWA DIAMOND IND. CO., LTD."}))
        self.assertTrue(rows["shipper_name"])
        rows = dict((k, ok) for k, ok, _ in dp.compare({"shipper_name": "EHWA STEEL"}, {"shipper_name": "EHWA DIAMOND"}))
        self.assertFalse(rows["shipper_name"])


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 스캔주인(unittest.TestCase):
    def test_인보이스가_그림뿐이면_통화는_스캔에서_온다(self):
        out = dp.merge_parts([("AWB", {"hawb": "H1"}), ("SCAN", {"currency": "USD", "hawb": "H9"})])
        self.assertEqual((out["currency"], out["hawb"]), ("USD", "H1"))
        self.assertNotIn("currency", dp.SPLIT_KEYS["AWB"])


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 그림묶음(unittest.TestCase):
    def test_큰_그림_묶음은_서너_쪽씩_나눈다(self):
        pages = []
        for i in range(1, 8):
            p = page("SCAN", "", i); pages.append(p)
        calls = []
        real = dp.call_claude_cli
        dp.call_claude_cli = lambda model, msgs, paths=None, schema=None, **kw: (calls.append(len(paths)), ({}, {"wall": 1}))[1]
        real_img = dp.scan_images
        dp.scan_images = lambda pg, dest, dpi=110: ["x%d.png" % i for i in range(len(pg))]
        try:
            dp.call_split("m", pages, [], {})
        finally:
            dp.call_claude_cli, dp.scan_images = real, real_img
        self.assertEqual(sorted(calls), [3, 4])

    def test_앞에_붙은_인코텀즈도_잡는다(self):
        self.assertEqual(dp.postprocess({"incoterms": "FOBSKHYCL"})["incoterms"], "FOB")


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 조각(unittest.TestCase):
    def test_같은_서류의_조각은_총계를_더하지_않는다(self):
        # 인보이스 두 쪽을 그림으로 나눠 불렀다. 둘 다 포장 수 27 을 냈다. 54 가 되면 안 된다.
        out = dp.merge_parts([("PL", {"pl_packages": 27, "pl_lines": [{"qty": 1}]}, 0, dp.SPLIT_KEYS["PL"]),
                              ("PL", {"pl_packages": 27, "pl_lines": [{"qty": 2}], "pl_gross": 334.64}, 0, dp.SPLIT_KEYS["PL"])])
        self.assertEqual((out["pl_packages"], out["pl_gross"]), (27, 334.64))
        self.assertEqual([l["qty"] for l in out["pl_lines"]], [1, 2])

    def test_같은_인보이스_사본이_여럿이면_줄을_한_번만_잇는다(self):
        # 스캔 묶음에 같은 인보이스가 세 부(HB, OH, LCA 사본). 사본마다 3줄을 읽어 왔다. 9줄이 되면
        # 금액 합이 총액의 3배가 되어 「빠진 줄 의심」이 뜬다(실제 서류 점검 11/6). 품명은 OCR 마다
        # 다르게 읽혀도 같은 사본이다.
        lines = [{"po_no": "4353379233", "qty": 30, "unit_price": 15.4, "amount": 462, "description": "SENSOR"},
                 {"po_no": "4353379236", "qty": 130, "unit_price": 5, "amount": 650, "description": "CABLE"}]
        # 두 번째 사본은 품명이 길고 부품 번호가 더 있으며 한 줄은 원산지가 빠졌다(스캔 OCR 차이). 그래도 같은 사본이다.
        copy2 = [dict(l, description=l["description"] + " ASSY LONG NAME", part_no="1295A0390") for l in lines]
        copy2[0]["country_of_origin"] = "US"
        # 세 번째 사본은 차례와 상자 번호가 더 읽혔다. 둘 다 가진 숫자와 번호가 같으면 같은 사본이다.
        lines = [dict(l, seq=i + 1, case_no="Packing#%d" % (i + 1)) for i, l in enumerate(lines)]
        out = dp.merge_parts([("CI", {"invoice_no": "SO26012102", "ci_total": 1112, "ci_lines": lines}, 0, dp.SPLIT_KEYS["CI"]),
                              ("CI", {"invoice_no": "SO26012102", "ci_total": 1112, "ci_lines": copy2}, 1, dp.SPLIT_KEYS["CI"]),
                              ("CI", {"invoice_no": "SO26012102", "ci_total": 1112, "ci_lines": lines}, 2, dp.SPLIT_KEYS["CI"])])
        self.assertEqual([l["po_no"] for l in out["ci_lines"]], ["4353379233", "4353379236"])
        self.assertEqual(out["ci_total"], 1112)

    def test_번호가_없는_같은_서류_두_부도_줄을_한_번만_잇는다(self):
        # 통관 용도설명서는 인보이스 번호가 없어 같은 서류로 못 묶인다. 두 부가 들어오면 통관표 줄이 두 배가 되어
        # 인보이스 7줄에 통관표 14줄로 「빠진 줄 의심」이 떴다(COKR26014064, 11/6).
        cn = [{"sap_code": "P3103668", "country_of_origin": "KR", "qty": 7}, {"sap_code": "P3103669", "country_of_origin": "KR", "qty": 1}]
        before = dp.PROMPT_VERSION
        dp.set_prompt("h1")      # 통관표 줄은 하이닉스 판 스키마에만 있다
        try:
            out = dp.merge_parts([("CUSTOMS", {"cn_lines": list(cn)}, 0, ["cn_lines"]), ("CUSTOMS", {"cn_lines": list(cn)}, 1, ["cn_lines"])])
            self.assertEqual(len(out["cn_lines"]), 2)
            # 한 줄짜리 통관표 세 부. 단위가 붙은 중량("0.24KG")도 숫자로 견준다(FLUSA4414, 11/6)
            one = [{"seq": 1, "sap_code": "F3180457", "model": "1295A0390", "country_of_origin": "USA", "net_weight": "0.24KG"}]
            out = dp.merge_parts([("CUSTOMS", {"cn_lines": list(one)}, i, ["cn_lines"]) for i in range(3)])
            self.assertEqual(len(out["cn_lines"]), 1)
            # 포장명세서 사본의 PO 번호를 OCR 이 다른 붙임표(‐)로 읽어도 같은 번호다
            pl_a = [{"sap_code": "F3180457", "po_no": "PO‐4353375584", "qty": 5, "net": 1.2, "gross": 2.7}]
            pl_b = [{"sap_code": "F3180457", "po_no": "PO-4353375584", "qty": 5, "net": 1.2, "gross": 2.7, "invoice_no": "CI265071040"}]
            out = dp.merge_parts([("PL", {"pl_lines": pl_a}, 0, dp.SPLIT_KEYS["PL"]), ("PL", {"pl_lines": pl_b}, 1, dp.SPLIT_KEYS["PL"])])
            self.assertEqual(len(out["pl_lines"]), 1)
            # 줄이 다른 두 서류는 여전히 이어진다
            out = dp.merge_parts([("CUSTOMS", {"cn_lines": cn[:1]}, 0, ["cn_lines"]), ("CUSTOMS", {"cn_lines": cn[1:]}, 1, ["cn_lines"])])
            self.assertEqual(len(out["cn_lines"]), 2)
        finally:
            dp.set_prompt(before)

    def test_사본마다_두_코드_칸이_바뀌어도_줄을_한_번만_잇는다(self):
        # 스캔 사본마다 AI 가 Part No 와 Material Code 를 서로 바꿔 넣어, 4줄 인보이스가 12줄로 읽혔다
        # (PNS26025031, 11/10). 숫자로만 된 코드("1024103")도 같은 묶음으로 견준다(PNS26071952).
        a = [{"po_no": "4303172339", "part_no": "D5CP91637", "material_code": "P3024498", "qty": 2, "unit_price": 121.81, "amount": 243.62},
             {"po_no": "4353405763", "part_no": "P3186599", "material_code": "1024103", "qty": 6, "unit_price": 10, "amount": 60}]
        b = [{"po_no": "4303172339", "part_no": "P3024498", "material_code": "D5CP91637", "qty": 2, "unit_price": 121.81, "amount": 243.62, "seq": 1},
             {"po_no": "4353405763", "part_no": "1024103", "material_code": "P3186599", "qty": 6, "unit_price": 10, "amount": 60, "seq": 2}]
        out = dp._merge_chunks([{"ci_lines": a}, {"ci_lines": b}, {"ci_lines": [dict(x) for x in a]}])
        self.assertEqual(len(out["ci_lines"]), 2)
        # SAP 코드가 한 사본에서는 Material Code 칸에, 다른 사본에서는 제 칸에 들어간 경우(PNS26071952 재판독)
        p = [{"po_no": "4353463630", "part_no": "5105-300130-B1", "material_code": "AA0500162", "qty": 2, "net": 2, "gross": 11}]
        q = [{"po_no": "4353463630", "sap_code": "AA0500162", "part_no": "5105-300130-B1 WIQ2101-0030A", "material_code": "P2170447", "qty": 2, "net": 2, "gross": 11}]
        out = dp._merge_chunks([{"pl_lines": p}, {"pl_lines": q}])
        self.assertEqual(len(out["pl_lines"]), 1)
        # 숫자가 모두 같아도 코드가 하나도 겹치지 않으면 다른 줄이라 그대로 잇는다
        c = [dict(x, part_no="X1", material_code="Y1") for x in a]
        out = dp._merge_chunks([{"ci_lines": a}, {"ci_lines": c}])
        self.assertEqual(len(out["ci_lines"]), 4)

    def test_사본_한_줄의_코드_오타는_봐주고_여러_줄이면_다른_서류다(self):
        # 15줄 포장명세서 사본 두 부 중 한 줄만 코드를 5105-200527 과 5105-300527 로 달리 읽었다(PNS26071952, 11/10)
        a = [{"po_no": f"43034{i:05d}", "part_no": f"5105-20{i:04d}-B1", "qty": i + 1, "net": 0.1 * (i + 1), "gross": 1.0 + i} for i in range(5)]
        b = [dict(x, material_code=f"P30{i:05d}") for i, x in enumerate(a)]
        b[2]["part_no"] = "5105-300002-B1"
        out = dp._merge_chunks([{"pl_lines": a}, {"pl_lines": b}])
        self.assertEqual(len(out["pl_lines"]), 5)
        # 두 줄의 코드가 다르면 사본으로 보지 않고 잇는다(빠짐 검사가 줄 수를 보고 판단하게 둔다)
        b[3]["part_no"] = "OTHER-1"
        out = dp._merge_chunks([{"pl_lines": a}, {"pl_lines": b}])
        self.assertEqual(len(out["pl_lines"]), 10)
        # 두 줄짜리 목록은 봐줄 근거가 모자라 코드 한 줄만 달라도 잇는다
        out = dp._merge_chunks([{"pl_lines": a[:2]}, {"pl_lines": [a[0], dict(a[1], part_no="Z9")]}])
        self.assertEqual(len(out["pl_lines"]), 4)

    def test_다른_서류면_여전히_더한다(self):
        out = dp.merge_parts([("PL", {"pl_packages": 21}, 0, dp.SPLIT_KEYS["PL"]), ("PL", {"pl_packages": 2}, 1, dp.SPLIT_KEYS["PL"])])
        self.assertEqual(out["pl_packages"], 23)

    def test_그림_인보이스는_포장명세서_칸도_받는다(self):
        a = page("CI", "x", 1); a.image = True
        ks = dp.group_keys("CI", [a])
        self.assertIn("pl_gross", ks); self.assertIn("ci_total", ks)
        self.assertNotIn("pl_gross", dp.group_keys("CI", [page("CI", "x", 1)]))

    def test_힌트가_같은_밀린_쪽은_한_서류다(self):
        a, b = page("CI", "x", 1), page("CI", "y", 2)
        a.image = b.image = True
        self.assertEqual([(k, len(p)) for k, p in dp.split_groups([a, b])], [("CI", 2)])


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 한글주문서(unittest.TestCase):
    def test_한글_발주서도_인보이스가_아니다(self):
        p = page("?", "공급업체: 308803\n발주일자: 2026-08-11\n발주번호: FCL4352406384\n구매자 : SK hynix\n납품장소\n인도조건 : EXW\nUnit price 33,000.00 Amount 33,000.00 USD")
        self.assertEqual(dp.classify(p).kind, "PO")


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 후처리꼴(unittest.TestCase):
    def test_통화는_세_글자다(self):
        for raw, want in (("US $", "USD"), ("USD", "USD"), ("US$", "USD"), ("RMB", "CNY"), ("JPY", "JPY")):
            self.assertEqual(dp.postprocess({"currency": raw})["currency"], want, raw)

    def test_PO_번호의_꼬리를_뗀다(self):
        self.assertEqual(dp.postprocess({"customer_po": "4353374643 (YHS-140  108 GAL)"})["customer_po"], "4353374643")
        self.assertEqual(dp.postprocess({"customer_po": "FCL4352403112"})["customer_po"], "FCL4352403112")

    def test_포장_수가_낱개_수량이면_AWB_피스를_쓴다(self):
        ex = dp.postprocess({"pl_packages": 28, "awb_pieces": 2, "pl_lines": [{"qty": 20}, {"qty": 8}]})
        self.assertEqual((ex["pl_packages"], ex["pl_packages_raw"]), (2, 28))
        # 포장 수와 피스가 같으면 안 건드린다
        self.assertEqual(dp.postprocess({"pl_packages": 3, "awb_pieces": 3, "pl_lines": [{"qty": 3}]})["pl_packages"], 3)

    def test_포장명세서에_찍힌_Purchase_Order_No_는_주문서가_아니다(self):
        p = page("?", "Packing list - 4000\nDelivery No. 815410986\nPurchase Order No: 4352400758\nGross Wt 2.0 kg\nNet Wt 1.5 kg\nPIECES 1\ncarton dimension")
        self.assertNotEqual(dp.classify(p).kind, "PO")

    def test_Page_1_of_2_는_같은_서류다(self):
        a = page("PL", "Packing list Page 1 of 2 TOTAL", 1); b = page("PL", "Page 2 of 2 more", 2)
        self.assertEqual([(k, len(pg)) for k, pg in dp.split_groups([a, b])], [("PL", 2)])


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 마스터번호(unittest.TestCase):
    def test_원문에서_검증_숫자가_맞는_번호를_찾는다(self):
        self.assertEqual(dp.find_mawb("180 ICN 5410 2893 PNS26031818"), "180-54102893")
        self.assertEqual(dp.find_mawb("843-KUL-45505611 DIM026076201"), "843-45505611")
        self.assertEqual(dp.find_mawb("MAWB NO : 18020201230"), "180-20201230")
        self.assertIsNone(dp.find_mawb("TEL 82-31-592-8681 FAX 12345678901"))

    def test_하우스_자리의_마스터는_옮긴다(self):
        ex = dp.postprocess({"hawb": "843-KUL-45505611", "mawb": None})
        self.assertEqual((ex["hawb"], ex["mawb"]), (None, "843-45505611"))
        ex = dp.postprocess({"hawb": "HDLCA114645", "mawb": "180 ICN 5410 2893"})
        self.assertEqual((ex["hawb"], ex["mawb"]), ("HDLCA114645", "180-54102893"))

    def test_PO_번호가_여럿이면_첫_번째(self):
        self.assertEqual(dp.postprocess({"customer_po": "4552325057 & 4552325058 & 4552325059"})["customer_po"], "4552325057")
        self.assertEqual(dp.postprocess({"customer_po": "4353368656, 4353365827"})["customer_po"], "4353368656")


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 하우스번호(unittest.TestCase):
    def test_원문에서_하우스_번호를_찾는다(self):
        self.assertEqual(dp.find_hawb("843-KUL-45505611 DIM026076201\nUSCI91320214717855313B", "843-45505611"), "DIM026076201")
        self.assertEqual(dp.find_hawb("HDLCA114645\nSHIPPER'S NAME", None), "HDLCA114645")
        self.assertIsNone(dp.find_hawb("TEL:82-31-592-8681 USCI91320214717855313B", None))


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 같은인보이스(unittest.TestCase):
    def test_같은_번호를_낸_두_묶음은_더하지_않는다(self):
        out = dp.merge_parts([("CI", {"invoice_no": "A1", "ci_total": 100, "pl_packages": 7}, 0, dp.SPLIT_KEYS["CI"] + ["pl_packages"]),
                              ("CI", {"invoice_no": "A1", "pl_packages": 7, "pl_gross": 53.6}, 1, dp.SPLIT_KEYS["CI"] + ["pl_packages", "pl_gross"])])
        self.assertEqual((out["ci_total"], out["pl_packages"], out["pl_gross"]), (100, 7, 53.6))

    def test_엔티티를_푼다(self):
        self.assertEqual(dp.postprocess({"item_desc": "PARTS &amp; ACCESSORIES"})["item_desc"], "PARTS & ACCESSORIES")


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 경계(unittest.TestCase):
    def test_버리는_쪽은_서류의_경계다(self):
        a, b, c, d = page("CI", "x", 1), page("CI", "y", 2), page("CUSTOMS", "z", 3), page("CI", "w", 4)
        for p in (a, b, d): p.image = True
        c.dropped = True
        g = dp.split_groups([a, b, c, d])
        self.assertEqual([(k, len(pg)) for k, pg in g], [("CI", 2), ("CI", 1)])


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 장소꼴(unittest.TestCase):
    def test_노선은_앞뒤를_가른다(self):
        ex = dp.postprocess({"origin": "NRT-PVG", "destination": "NRT-PVG"})
        self.assertEqual((ex["origin"], ex["destination"]), ("NRT", "PVG"))

    def test_코드가_이름_앞에_붙으면_뗀다(self):
        self.assertEqual(dp.postprocess({"origin": "HKG HONG KONG"})["origin"], "HONG KONG")
        self.assertEqual(dp.postprocess({"origin": "ICN INCHEON AIRPORT,KOREA"})["origin"], "INCHEON AIRPORT,KOREA")
        self.assertEqual(dp.postprocess({"destination": "PVG"})["destination"], "PVG")


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 그림OTHER(unittest.TestCase):
    def test_종류_모르는_그림_쪽은_글자_AWB_에_붙지_않는다(self):
        a, b = page("AWB", "awb text", 1), page("OTHER", "x", 2)
        b.image = True
        self.assertEqual([(k, len(pg)) for k, pg in dp.split_groups([a, b])], [("AWB", 1), ("SCAN", 1)])
