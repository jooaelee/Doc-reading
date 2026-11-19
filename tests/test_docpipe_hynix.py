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

    def test_코드가_없어도_같은_인보이스의_같은_수량이면_잇는다(self):
        """AATSN2601238: 인보이스에만 코드가 있고 포장명세서 줄은 코드도 PO 도 없다. 같은 264개라 한 줄이다."""
        ci = [줄(invoice_no="KA-1", sap_code="1014869", qty=264)]
        pl = [{"invoice_no": None, "qty": 264, "net": 9.24, "gross": 12, "description": "LENS"}]
        rows = G.item_rows("케이스", {"ci_lines": ci, "pl_lines": pl})
        self.assertEqual([r["출처"] for r in rows], ["CI"])
        self.assertEqual((rows[0]["순중량(kg)"], rows[0]["총중량(kg)"]), (9.24, 12))

    def test_인보이스와_포장명세서의_코드_종류가_달라도_잇는다(self):
        """AATSN2602139: 인보이스에는 SAP CODE 1014866, 포장명세서에는 Part No. 만 찍혔다. 같은 124개다."""
        ci = [줄(invoice_no="KA-2", sap_code="1014866", qty=124)]
        pl = [{"invoice_no": None, "part_no": "SMJL-ST3M37HE-5428", "qty": 124, "net": 4.34}]
        rows = G.item_rows("케이스", {"ci_lines": ci, "pl_lines": pl})
        self.assertEqual([r["출처"] for r in rows], ["CI"])

    def test_시리얼이_같으면_잇고_다르면_잇지_않는다(self):
        """HDLCA112889: 같은 부품 넷이 시리얼만 다르다. 포장명세서는 시리얼 칸에 모델까지 붙여 찍는다."""
        ci = [줄(invoice_no="P1", part_no="4S267", serial_no=s, qty=1) for s in ("390", "412")]
        pl = [{"invoice_no": None, "material_code": "10", "serial_no": "[4S267 ] 412", "qty": 1, "net": 4},
              {"invoice_no": None, "material_code": "20", "serial_no": "[4S267 ] 390", "qty": 1, "net": 5}]
        rows = G.item_rows("케이스", {"ci_lines": ci, "pl_lines": pl})
        self.assertEqual([(r["출처"], r["Serial No."], r["순중량(kg)"]) for r in rows], [("CI", "390", 5), ("CI", "412", 4)])

    def test_수량이_다르거나_인보이스가_다르면_잇지_않는다(self):
        """상자별로 나눈 포장 줄(20개, 17개)은 37개 인보이스 줄에 붙이지 않는다. 다른 장의 같은 수량에도 붙이지 않는다."""
        ci = [줄(invoice_no="I1", sap_code="S1", qty=37), 줄(invoice_no="I2", sap_code="S2", qty=5)]
        pl = [{"invoice_no": "I1", "qty": 20}, {"invoice_no": "I1", "qty": 17}, {"invoice_no": "I1", "qty": 5},
              {"invoice_no": None, "qty": 5}]
        rows = G.item_rows("케이스", {"ci_lines": ci, "pl_lines": pl})
        self.assertEqual([r["출처"] for r in rows], ["CI", "CI", "PL", "PL", "PL", "PL"])   # 장이 둘이라 번호 없는 줄도 안 붙인다

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


class 하이닉스_자재_번호(unittest.TestCase):
    """Main 의 Material Code 는 열 이름이 아니라 하이닉스 자재 번호 형식으로 고른다(고객 PPT ’26.11.04)."""

    def mat(self, ex):
        return [r["Material"] for r in G.item_rows("케이스", ex)]

    def test_Sap_Code_열에_공급사_코드가_있으면_Material_열의_하이닉스_번호를_쓴다(self):
        """PNS26025031: Sap Code 열에 AA0400575, Material 열에 P3024498. 통관표에는 P3024498."""
        self.assertEqual(self.mat({"ci_lines": [줄(sap_code="AA0400575", material_code="P3024498")]}), ["P3024498"])

    def test_통관표에_있는_코드를_먼저_쓴다(self):
        ex = {"ci_lines": [줄(sap_code="SZ967770", material_code="P3192754")],
              "cn_lines": [{"sap_code": "P3192754"}]}
        self.assertEqual(self.mat(ex), ["P3192754"])
        self.assertEqual(G.item_rows("케이스", ex)[0]["_mat_key"], "material_code")

    def test_여러_모양의_하이닉스_번호(self):
        for code in ("R1257986", "SZ967770", "S31A0156", "MMF09732", "EBE05551"):
            self.assertTrue(G.is_hynix_code(code), code)
        for code in ("AA0400575", "23-10101-55-JGN", "BISV", "R318259"):
            self.assertFalse(G.is_hynix_code(code), code)

    def test_공급사_자재_번호는_넣지_않고_까닭을_적는다(self):
        """PPT 3쪽 4번: 자재 모델은 표시되나 Material Code 미표시(Entegris 23-10101-55-JGN)."""
        ex = {"ci_lines": [줄(material_code="23-10101-55-JGN", po_no="4303517321")]}
        self.assertEqual(self.mat(ex), [""])
        req = G.req_rows("케이스", ex)[0]
        self.assertEqual(req["Material Code"], "")
        self.assertIn("23-10101-55-JGN", req["미추출 사유"])

    def test_부품_번호_칸으로_바뀌어_읽힌_번호도_찾는다(self):
        """사본마다 Part No 와 Material 칸이 바뀌어 읽힌다(PNS26025031)."""
        self.assertEqual(self.mat({"ci_lines": [줄(part_no="P3024498", material_code="D5CP91637")]}), ["P3024498"])

    def test_부품_번호_칸이_고객_자재_번호_열이면_다른_모양도_받는다(self):
        """WUX-W260200: 料件号(공급사 번호 3000527)와 客户料号(S2M21062, P3004049 ...)를 같이 찍는다."""
        ci = [줄(material_code="3000527", part_no="S2M21062"), 줄(material_code="4400084", part_no="P3004049"),
              줄(material_code="1766531", part_no="P1307809")]
        self.assertEqual(self.mat({"ci_lines": ci}), ["S2M21062", "P3004049", "P1307809"])
        # 부품 번호 칸이 하이닉스 번호 열이 아니면 S2M21062 같은 모양은 공급사 부품 번호일 수 있어 받지 않는다
        self.assertEqual(self.mat({"ci_lines": [줄(part_no="S2M21062")]}), [""])

    def test_형식_밖_코드도_붙임표가_없으면_차선으로_남긴다(self):
        """하이닉스 번호 모양을 다 본 것은 아니다. 가짜 서류의 F149A0001 같은 값을 지우지 않는다."""
        self.assertEqual(self.mat({"ci_lines": [줄(sap_code="F149A0001")]}), ["F149A0001"])

    def test_사람이_Material_을_고치면_고른_칸에_배운다(self):
        import wuxi_learn as L
        row = G.item_rows("케이스", {"ci_lines": [줄(sap_code="AA0400575", material_code="P3024498")]})[0]
        self.assertEqual(L.field_for("Material", row), "material_code")
        self.assertEqual(L.field_for("Material", {"Sap Code": "S1"}), "sap_code")    # 예전 줄


class 통화(unittest.TestCase):
    def test_화면에서_Net_Price_다음에_Currency_다음에_Invoice_No(self):
        cols = [c for c in G.GERP_ITEM_COLS + G.EXTRA_ITEM_COLS if c != "케이스"]   # 고객 화면은 케이스를 숨긴다
        i = cols.index("Net Price")
        self.assertEqual(cols[i:i + 3], ["Net Price", "Currency", "Invoice No."])

    def test_줄마다_그_인보이스의_통화를_잇는다(self):
        ex = {"ci_lines": [줄(invoice_no="I1"), 줄(invoice_no="I2")],
              "pl_lines": [{"invoice_no": None, "part_no": "X", "qty": 1}],
              "invoice_totals": [{"invoice_no": "I1", "total": 1, "currency": "USD"},
                                 {"invoice_no": "I2", "total": 1, "currency": "JPY"}]}
        rows = G.item_rows("케이스", ex)
        self.assertEqual([r["Currency"] for r in rows], ["USD", "JPY", ""])   # 어느 장인지 모르는 줄은 비운다
        ex["currency"] = "USD"
        self.assertEqual(G.item_rows("케이스", ex)[2]["Currency"], "USD")      # 머리 통화가 있으면 그 값

    def test_통화_열_전에_읽은_줄도_인보이스_표로_채운다(self):
        items = [{"Invoice No.": "I1"}, {"Invoice No.": "I9"}, {"Invoice No.": "I1", "Currency": "EUR"}]
        n = G.add_currency(items, [{"Invoice No.": "I1", "통화": "USD"}])
        self.assertEqual([r.get("Currency") for r in items], ["USD", "USD", "EUR"])   # 한 장뿐이면 그 통화, 있는 값은 그대로
        self.assertEqual(n, 2)


class NCV_단서(unittest.TestCase):
    def test_무상_문구와_단가_0을_적는다(self):
        self.assertEqual(G.ncv_hint(줄(description="EMPTY DRUM(200L) (NO COMMERCIAL VALUE)")), "NO COMMERCIAL VALUE")
        self.assertIn("N.C.V", G.ncv_hint(줄(description="47L Cylinder N.C.V.")))
        self.assertEqual(G.ncv_hint(줄(unit_price=0, qty=10)), "단가 0")
        self.assertEqual(G.ncv_hint(줄(description="PUMP SEAL KIT")), "")


class Main_Sub_시트(unittest.TestCase):
    def test_엑셀에_Main_Sub_정의_시트가_있다(self):
        import tempfile

        import openpyxl
        ex = {"ci_lines": [줄(po_no="4303368246", material_code="R3170652", part_no="112281", qty=80,
                             description="MIXTURE OF GASES"),
                           줄(seq=2, description="47L Steel Cylinder N.C.V.", qty=117, unit_price=125)]}
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "t.xlsx")
            G.write_xlsx([{"case": "케이스", "ex": ex}], out)
            wb = openpyxl.load_workbook(out)
            for name in ("Main 항목", "Sub 항목", "빈칸 진단", "항목 정의"):
                self.assertIn(name, wb.sheetnames)
            ws = wb["Main 항목"]
            head = [c.value for c in ws[1]]
            first = dict(zip(head, [c.value for c in ws[2]]))
            self.assertEqual((first["PO No."], first["Material Code"], first["부품 번호"], first["납품 수량"]),
                             ("4303368246", "R3170652", "112281", 80))
            sub = dict(zip([c.value for c in wb["Sub 항목"][1]], [c.value for c in wb["Sub 항목"][3]]))
            self.assertIn("N.C.V", sub["NCV 단서"])
            self.assertEqual(wb["항목 정의"].max_row, len(G.ITEM_SPEC) + 1)

    def test_화면에서_고친_줄만_받아도_Main_시트가_찬다(self):
        """고객 화면 엑셀은 고친 품목 줄만 넘긴다. 고친 값이 Main 시트에도 가야 한다."""
        import tempfile

        import openpyxl
        row = {c: "" for c in G.ITEM_COLS}
        row.update({"출처": "CI", "Purchase Order": "PO1", "Material": "R1257986", "B/L quantity": 3})
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "t.xlsx")
            G.write_tables({"items": [row], "head": [], "inv": [], "review": []}, out)
            ws = openpyxl.load_workbook(out)["Main 항목"]
            got = dict(zip([c.value for c in ws[1]], [c.value for c in ws[2]]))
            self.assertEqual((got["PO No."], got["Material Code"], got["납품 수량"]), ("PO1", "R1257986", 3))


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


class 편명_원문_대조(unittest.TestCase):
    """ORI07793063: AWB 가 항공사 LH 와 편 번호 687/21.02 를 다른 칸에 찍어 LH687 이 「원문에 없는 값」으로 떴다."""

    def 등급(self, text, flight="LH687"):
        res = D.verify({"flight1": flight}, [한_쪽(text, "AWB")], {})
        return next(g for g, k, _ in res if k == "flight1")

    def test_항공사와_번호가_따로_찍혀도_맞다(self):
        self.assertEqual(self.등급("PVG | LH | USD | CC | Shanghai-Pudong | 687/21.02 | 8400/23.02"), "OK")

    def test_지어낸_편명은_여전히_잡는다(self):
        """번호가 다른 숫자의 일부(1,687)이거나 항공사 코드가 없으면 맞은 것으로 보지 않는다."""
        self.assertEqual(self.등급("PVG | KE | USD | 1,687.00 | 21.02"), "FAIL")
        self.assertEqual(self.등급("PVG | LH | USD | 16870 | 21.02"), "FAIL")
        self.assertEqual(self.등급("PVG | LH | USD | 687 | 21.02", flight="OZ687"), "FAIL")


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
    """항목 정의. ’26.11.04 고객 PPT 의 추출 대상이 Main, 나머지가 Sub(11/10 다시 정함)."""

    def tearDown(self):
        D.set_prompt("v3")

    def test_Main_항목은_고객_추출_대상_순서다(self):
        """품명/모델은 정확도를 따로 재려고 두 칸으로 나눴다."""
        self.assertEqual([s[1] for s in G.MAIN_ITEMS],
                         ["BL No.", "PO No.", "Material Code", "품명", "부품 번호", "납품 수량", "단위", "단가"])

    def test_항목마다_BL_Item_열이_있다(self):
        for tier, label, col, how, _, note in G.ITEM_SPEC:
            self.assertIn(col, G.ITEM_COLS, label)
            self.assertIn(how, G.COMPARE_NAME, label)
            self.assertTrue(note, label)

    def test_9월_메일의_열_항목이_모두_들어_있다(self):
        """9/30 메일의 열 개 가운데 Material No 와 Sap Code 는 같은 하이닉스 자재 번호라 하나가 됐다."""
        names = {s[1] for s in G.ITEM_SPEC}
        for n in ("PO No.", "Material Code", "시리얼 번호", "품명", "납품 수량", "단위", "단가", "금액", "원산지"):
            self.assertIn(n, names)

    def test_시리얼_번호를_받는다(self):
        """요구 항목 넷째다. 판을 만들 때 스키마에 아예 없었다."""
        D.set_prompt("h1")
        self.assertIn("serial_no", D.SCHEMA["properties"]["ci_lines"]["items"]["properties"])

    def test_자재번호와_SAP코드를_따로_받는다(self):
        """한 표에 MATERIAL 열과 SAP Code 열이 같이 찍히는 서류가 있다. 두 열을 원문 그대로 같이 싣는다."""
        D.set_prompt("h1")
        import gerp_xlsx as X
        r = X.item_rows("케이스", {"ci_lines": [줄(material_code="M1", sap_code="S1")], "pl_lines": []})[0]
        self.assertEqual(r["Material Code"], "M1")
        self.assertEqual(r["Sap Code"], "S1")

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
