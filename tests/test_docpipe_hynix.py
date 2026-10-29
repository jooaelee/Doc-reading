# -*- coding: utf-8 -*-
"""하이닉스 중국법인 판(h1)을 잠근다.

🔴 왜 잠그나. 이 판은 줄 단위 값을 뽑아 GERP 품목 표로 보낸다. 만들면서 세 번 틀렸고,
   셋 다 조용히 틀렸다. 표는 멀쩡히 나오는데 값이 틀린 꼴이라 눈으로는 안 잡힌다.

   1. 후처리가 줄 안의 글자를 전부 숫자로 바꿨다. 예외가 ("description", "code") 두
      이름으로 박혀 있었다. 인보이스 번호 "2026-01-MSP_WUXI" 가 2026 이 되고 Sap Code
      "P3103668" 이 3103668 이 됐다. 검증은 그대로 통과했다.
   2. 서류별로 나눠 부른 결과를 합칠 때 새 배열 둘(po_list, invoice_totals)이 CAT_KEYS 에
      없어서 마지막 호출 것만 남았다. 인보이스 네 장 중 한 장의 총액만 남았다.
   3. 인보이스 줄과 포장명세서 줄을 품목 코드로만 이었다. 열일곱 줄의 코드가 전부 같은
      서류에서 열여섯 줄이 짝을 잃었다.

   그리고 기존 판(v2~v6)이 그대로여야 한다. 실측값을 그 판으로 쟀다.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import docpipe as D
import gerp_xlsx as G


class 한_쪽(object):
    """verify 가 원문 대조에 쓰는 쪽. 실제 PDF 없이 글만 준다."""

    def __init__(self, text, kind="CI"):
        self.text = text
        self.kind = kind
        self.ocr = ""
        self.image = False


def 줄(**kw):
    base = dict(seq=1, invoice_no="INV-1", sap_code="", po_no="", po_item_no="",
                iv_no="", hios_no="", material_code="", part_no="",
                description="ITEM", qty=1, unit="EA", unit_price=1.0, amount=1.0,
                hs_code=None, country_of_origin=None)
    base.update(kw)
    return base


class 판이_갈려_있다(unittest.TestCase):
    def tearDown(self):
        D.set_prompt("v3")

    def test_h1_이_프로필에_있다(self):
        self.assertIn("h1", D.PROFILES)

    def test_h1_줄에_인쇄된_열_이름이_다_있다(self):
        D.set_prompt("h1")
        ci = D.SCHEMA["properties"]["ci_lines"]["items"]["properties"]
        for k in ("invoice_no", "sap_code", "po_no", "po_item_no", "iv_no", "hios_no"):
            self.assertIn(k, ci)

    def test_기존_판은_줄_칸이_그대로다(self):
        """v4 에 칸이 늘면 그 판으로 잰 실측값과 견줄 수 없게 된다."""
        D.set_prompt("v4")
        ci = set(D.SCHEMA["properties"]["ci_lines"]["items"]["properties"])
        self.assertEqual(ci, {"description", "qty", "unit_price", "amount"})
        self.assertNotIn("po_list", D.SCHEMA["properties"])

    def test_줄_설명은_h1_에만_붙는다(self):
        D.set_prompt("v4")
        v4 = D.SCHEMA["properties"]["ci_lines"]["items"]["properties"]["description"]
        self.assertNotIn("description", v4)          # 설명 열쇠가 안 붙는다
        D.set_prompt("h1")
        h1 = D.SCHEMA["properties"]["ci_lines"]["items"]["properties"]["sap_code"]
        self.assertIn("Sap Code", h1["description"])

    def test_표_정렬은_h1_에서만_켜진다(self):
        """표의 열 머리글이 값보다 뒤에 찍히는 서류가 있다. 정렬본이라야 열이 남는다."""
        D.set_prompt("v6")
        self.assertFalse(D.TEXT_FIX["table_sort"])
        D.set_prompt("h1")
        self.assertTrue(D.TEXT_FIX["table_sort"])


class 후처리가_글자를_안_망친다(unittest.TestCase):
    def tearDown(self):
        D.set_prompt("v3")

    def test_줄의_글자_칸이_숫자로_안_바뀐다(self):
        D.set_prompt("h1")
        ex = D.postprocess({"ci_lines": [줄(invoice_no="2026-01-MSP_WUXI", sap_code="P3103668",
                                            po_no="4303400870", po_item_no="00010")]})
        ln = ex["ci_lines"][0]
        self.assertEqual(ln["invoice_no"], "2026-01-MSP_WUXI")
        self.assertEqual(ln["sap_code"], "P3103668")
        self.assertEqual(ln["po_item_no"], "00010")   # 앞의 0 이 날아가면 GERP 품목 번호가 아니다

    def test_숫자_칸은_여전히_숫자가_된다(self):
        D.set_prompt("h1")
        ex = D.postprocess({"ci_lines": [줄(qty="300", amount="1,557.00")]})
        self.assertEqual(ex["ci_lines"][0]["qty"], 300)
        self.assertEqual(ex["ci_lines"][0]["amount"], 1557)

    def test_기존_판의_줄도_하던_대로_돈다(self):
        D.set_prompt("v4")
        ex = D.postprocess({"ci_lines": [{"description": "A-1", "qty": "2", "unit_price": "3.5",
                                          "amount": "7.0"}]})
        ln = ex["ci_lines"][0]
        self.assertEqual(ln["description"], "A-1")
        self.assertEqual((ln["qty"], ln["unit_price"], ln["amount"]), (2, 3.5, 7))

    def test_건_단위_배열의_겹친_것을_뺀다(self):
        """나눠 부르면 인보이스 네 장이 네 번 답한다. 같은 PO 가 그만큼 되풀이된다."""
        D.set_prompt("h1")
        ex = D.postprocess({"po_list": ["PO-1", "PO-2", "PO-1", " PO-2 ", ""],
                            "invoice_totals": [{"invoice_no": "I1", "total": 1, "currency": "USD"},
                                               {"invoice_no": "I1", "total": 1, "currency": "USD"},
                                               {"invoice_no": "I2", "total": 2, "currency": "USD"}]})
        self.assertEqual(ex["po_list"], ["PO-1", "PO-2"])
        self.assertEqual([t["invoice_no"] for t in ex["invoice_totals"]], ["I1", "I2"])

    def test_나눠_부른_배열이_이어_붙는다(self):
        for k in ("po_list", "invoice_totals"):
            self.assertIn(k, D.CAT_KEYS)


class 줄_단위_검증(unittest.TestCase):
    def tearDown(self):
        D.set_prompt("v3")

    def 등급(self, res, key):
        for g, k, _ in res:
            if k == key:
                return g
        return None

    def test_인보이스_장마다_합을_따로_맞춘다(self):
        """한 묶음에 인보이스 네 장이 오면 전체 합만 보는 규칙으로는 장별 오류가 안 보인다."""
        D.set_prompt("h1")
        ex = {"ci_lines": [줄(invoice_no="I1", amount=10), 줄(invoice_no="I2", amount=5)],
              "invoice_totals": [{"invoice_no": "I1", "total": 10, "currency": "USD"},
                                 {"invoice_no": "I2", "total": 7, "currency": "USD"}],
              "ci_total": 17}
        res = D.verify(ex, [한_쪽("I1 I2")], {})
        self.assertEqual(self.등급(res, "inv_total[I1]"), "OK")
        self.assertEqual(self.등급(res, "inv_total[I2]"), "FAIL")
        self.assertEqual(self.등급(res, "ci_total_vs_invoices"), "OK")

    def test_줄에_PO_가_비면_짚는다(self):
        D.set_prompt("h1")
        ex = {"ci_lines": [줄(po_no="4303400870", sap_code="P1"), 줄(po_no="", sap_code="P1")]}
        res = D.verify(ex, [한_쪽("4303400870 P1")], {})
        self.assertEqual(self.등급(res, "ci_lines_po_no"), "WARN")
        self.assertEqual(self.등급(res, "ci_lines_code"), "OK")

    def test_원문에_없는_줄_PO_를_잡는다(self):
        D.set_prompt("h1")
        ex = {"ci_lines": [줄(po_no="9999999999", sap_code="P1")]}
        res = D.verify(ex, [한_쪽("4303400870 P1")], {})
        self.assertEqual(self.등급(res, "line_po_in_text"), "FAIL")

    def test_기존_판에서는_줄_규칙이_안_돈다(self):
        """칸이 없는 판에 이 규칙을 걸면 멀쩡한 케이스가 매번 걸린다."""
        D.set_prompt("v4")
        res = D.verify({"ci_lines": [{"description": "A", "qty": 1, "unit_price": 1, "amount": 1}]},
                       [한_쪽("A")], {})
        self.assertIsNone(self.등급(res, "ci_lines_po_no"))


class GERP_표(unittest.TestCase):
    def test_서류에서_오는_열이_여섯이다(self):
        """Imp.Req. No. 는 GERP 수입의뢰 쪽에서 온다. 채운 척하면 틀린 값이 ERP 로 간다."""
        self.assertEqual(G.GERP_ONLY, ["Imp.Req. No."])
        self.assertEqual(len(G.GERP_ITEM_COLS) - len(G.GERP_ONLY), 7)

    def test_GERP_열이_줄의_어느_칸에서_오는지_한_곳에_있다(self):
        self.assertEqual(G.MAP_KEY["Purchase Order"], ("po_no",))
        self.assertEqual(G.MAP_KEY["Item"], ("po_item_no",))
        self.assertEqual(G.MAP_KEY["Material"], ("sap_code", "material_code"))

    def test_코드가_같아도_줄이_하나씩_이어진다(self):
        """열일곱 줄의 품목 코드가 전부 같은 서류가 있다. 코드만으로 이으면 한 줄에 몰린다."""
        ci = [줄(invoice_no="I1", sap_code="P1", po_no="PO1", po_item_no="00010", amount=1),
              줄(invoice_no="I1", sap_code="P1", po_no="PO1", po_item_no="00020", amount=2)]
        pl = [{"invoice_no": "I1", "sap_code": "P1", "po_no": "PO1", "po_item_no": "00010",
               "case_no": "C1", "qty": 1, "net": 1.0, "gross": 2.0, "description": "A", "unit": "EA"},
              {"invoice_no": "I1", "sap_code": "P1", "po_no": "PO1", "po_item_no": "00020",
               "case_no": "C2", "qty": 1, "net": 3.0, "gross": 4.0, "description": "A", "unit": "EA"}]
        rows = G.item_rows("케이스", {"ci_lines": ci, "pl_lines": pl})
        self.assertEqual(len(rows), 2)                       # 짝 없는 줄이 안 생긴다
        self.assertEqual([r["출처"] for r in rows], ["CI", "CI"])
        self.assertEqual([r["Case No."] for r in rows], ["C1", "C2"])
        self.assertEqual([r["순중량(kg)"] for r in rows], [1.0, 3.0])

    def test_포장명세서에만_있는_줄은_따로_나온다(self):
        ci = [줄(sap_code="P1", po_no="PO1", amount=1)]
        pl = [{"invoice_no": "INV-1", "sap_code": "P9", "po_no": "PO9", "po_item_no": "",
               "case_no": "C9", "qty": 1, "net": 1.0, "gross": 2.0, "description": "팔레트",
               "unit": "EA"}]
        rows = G.item_rows("케이스", {"ci_lines": ci, "pl_lines": pl})
        self.assertEqual([r["출처"] for r in rows], ["CI", "PL"])
        self.assertEqual(rows[1]["Invoice amount"], "")      # 금액은 인보이스에만 있다

    def test_장별_합을_표로_낸다(self):
        ex = {"ci_lines": [줄(invoice_no="I1", amount=10), 줄(invoice_no="I1", amount=5)],
              "invoice_totals": [{"invoice_no": "I1", "total": 15, "currency": "USD"}]}
        rows = G.invoice_rows("케이스", ex)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["줄 수"], rows[0]["줄 금액 합"], rows[0]["차이"]), (2, 15, 0))


if __name__ == "__main__":
    unittest.main()


class 데모_화면(unittest.TestCase):
    """`tools/hynix_demo.py` 의 값 만드는 자리. 서버를 띄우지 않고 함수만 본다."""

    def test_고친_값이_그대로_엑셀로_간다(self):
        """화면에서 고쳐도 안 담기면 확인 화면이 아니라 구경 화면이다."""
        import tempfile

        import openpyxl

        import gerp_xlsx as X
        rows = [{c: "" for c in X.ITEM_COLS}]
        rows[0].update({"Imp.Req. No.": "사람이 적은 값", "Purchase Order": "PO1",
                        "Material": "M1", "B/L quantity": 3})
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "t.xlsx")
            X.write_tables({"items": rows, "head": [], "inv": [], "review": []}, out)
            ws = openpyxl.load_workbook(out)["BL Item"]
            i = [c.value for c in ws[1]].index("Imp.Req. No.")
            self.assertEqual(ws.cell(row=2, column=i + 1).value, "사람이 적은 값")

    def test_못_잇는_줄이_표시된다(self):
        import tempfile

        import openpyxl

        import gerp_xlsx as X
        ok = {c: "" for c in X.ITEM_COLS}; ok.update({"Purchase Order": "PO1", "Material": "M1"})
        gap = {c: "" for c in X.ITEM_COLS}; gap.update({"Purchase Order": "", "Material": "M1"})
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "t.xlsx")
            s = X.write_tables({"items": [ok, gap], "head": [], "inv": [], "review": []}, out)
            self.assertEqual(s["gaps"], 1)
            ws = openpyxl.load_workbook(out)["BL Item"]
            self.assertEqual(ws.cell(row=3, column=1).fill.fgColor.rgb[-6:], "FFF2CC")

    def test_표본_목록이_없어도_안_죽는다(self):
        """서류 폴더가 없는 자리에서도 화면은 떠야 한다."""
        import hynix_demo as H
        old = H.DOC_ROOTS
        try:
            H.DOC_ROOTS = ["/없는/폴더"]
            self.assertEqual(H.sample_list(), [])
        finally:
            H.DOC_ROOTS = old


class 인보이스_번호_혼동(unittest.TestCase):
    def tearDown(self):
        D.set_prompt("v3")

    def test_인보이스_번호_자리에_PO_가_오면_짚는다(self):
        """같은 표에 나란히 찍혀 있어서 실제로 섞인다. 장별 합이 맞아도 장을 잘못 가른 것이다."""
        D.set_prompt("h1")
        ex = {"ci_lines": [줄(invoice_no="PO-A", po_no="PO-A", sap_code="S1", amount=10)],
              "invoice_totals": [{"invoice_no": "PO-A", "total": 10, "currency": "USD"}],
              "po_list": ["PO-A"], "ci_total": 10}
        res = D.verify(ex, [한_쪽("PO-A S1")], {})
        self.assertIn(("WARN", "invoice_no_looks_like_po"),
                      [(g, k) for g, k, _ in res if k == "invoice_no_looks_like_po"])


class PO_가_아닌_것을_거른다(unittest.TestCase):
    def tearDown(self):
        D.set_prompt("v3")

    def test_숫자_없는_값은_PO_가_아니다(self):
        """운송장의 N.C.V(신고가액 없음)가 줄의 PO 번호로 들어온 적이 있다."""
        D.set_prompt("h1")
        ex = D.postprocess({"ci_lines": [줄(po_no="N.C.V", sap_code="P1"),
                                         줄(po_no="4303400870", sap_code="P1")],
                            "po_list": ["N.C.V", "4303400870"]})
        self.assertIsNone(ex["ci_lines"][0]["po_no"])
        self.assertEqual(ex["ci_lines"][1]["po_no"], "4303400870")
        self.assertEqual(ex["po_list"], ["4303400870"])


class 고객_요구_열_항목(unittest.TestCase):
    """’26.09.30 하이닉스 우시 물류팀이 확정한 항목 열 개(임지원 PM 메일)."""

    def tearDown(self):
        D.set_prompt("v3")

    def test_열_항목이_다_있다(self):
        D.set_prompt("h1")
        import gerp_xlsx as X
        ci = D.SCHEMA["properties"]["ci_lines"]["items"]["properties"]
        for _, key in X.REQ_MAP:
            self.assertIn(key, ci, key)

    def test_시리얼_번호를_받는다(self):
        """요구 항목 넷째다. 판을 만들 때 스키마에 아예 없었다."""
        D.set_prompt("h1")
        self.assertIn("serial_no", D.SCHEMA["properties"]["ci_lines"]["items"]["properties"])

    def test_자재번호와_SAP코드를_따로_받는다(self):
        """한 표에 MATERIAL 열과 SAP Code 열이 같이 찍히는 서류가 있다. 둘 다 요구 항목이다."""
        D.set_prompt("h1")
        import gerp_xlsx as X
        r = X.item_rows("케이스", {"ci_lines": [줄(material_code="M1", sap_code="S1")], "pl_lines": []})[0]
        self.assertEqual(r["Material No"], "M1")
        self.assertEqual(r["SK HYNIX Sap Code"], "S1")

    def test_서류에_한_번만_찍힌_원산지를_줄에_채운다(self):
        """표에는 없고 머리에 「Country of Origin : KOREA」 한 줄만 있는 서류가 있다."""
        D.set_prompt("h1")
        ex = {"ci_lines": [줄(country_of_origin=None)]}
        D.hynix_fill(ex, "Country of Origin : KOREA   Forwarder : X" + " 가" * 200)
        self.assertEqual(ex["ci_lines"][0]["country_of_origin"], "KOREA")

    def test_근거_없는_SAP_코드를_지운다(self):
        """서류에 sap 라는 글자가 없는데 값이 들어온 적이 있다. 빈칸보다 나쁘다."""
        D.set_prompt("h1")
        ex = {"ci_lines": [줄(sap_code="P3187097")]}
        D.hynix_fill(ex, "INVOICE" + " 상세 내용" * 200)
        self.assertIsNone(ex["ci_lines"][0]["sap_code"])

    def test_스캔_서류에서는_지우지_않는다(self):
        """글자층이 없으면 근거를 글에서 못 찾는다. 없다고 지우면 멀쩡한 값이 날아간다."""
        D.set_prompt("h1")
        ex = {"ci_lines": [줄(sap_code="P3187097")]}
        D.hynix_fill(ex, "")
        self.assertEqual(ex["ci_lines"][0]["sap_code"], "P3187097")
