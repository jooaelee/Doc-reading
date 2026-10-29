"""10/29 이관 때 회사 docagent 에서 합친 규칙을 잠근다. 값은 모두 지어낸 것이다."""
import unittest

import docpipe as D


class 통관표_짝짓기(unittest.TestCase):
    def test_앞_8자가_한_줄에만_걸리면_잇는다(self):
        ex = {"cn_lines": [{"desc_en": "WAFER CARRIER BOX TYPE A", "sap_code": "F0100F001"}],
              "ci_lines": [{"description": "WAFER CARRIER 25 SLOT"}, {"description": "OTHER PART"}]}
        D.hynix_merge_cn(ex)
        self.assertEqual(ex["ci_lines"][0].get("sap_code"), "F0100F001")

    def test_앞_8자가_여러_줄에_걸리면_고르지_않는다(self):
        """앞 판은 처음 걸린 줄의 코드를 붙였다. 품명 앞부분이 같은 품목이 여럿이면 남의 코드가 된다."""
        ex = {"cn_lines": [{"desc_en": "VALVE ASSY SMALL", "sap_code": "F0100F001"},
                           {"desc_en": "VALVE ASSY LARGE", "sap_code": "F0100F002"},
                           {"desc_en": "GASKET", "sap_code": "F0100F003"}],
              "ci_lines": [{"description": "VALVE ASSY MEDIUM"}]}
        D.hynix_merge_cn(ex)
        self.assertFalse(ex["ci_lines"][0].get("sap_code"))

    def test_여러_줄이_걸려도_줄_수가_같고_같은_순번이_후보면_잇는다(self):
        ex = {"cn_lines": [{"desc_en": "VALVE ASSY SMALL", "sap_code": "F0100F001"},
                           {"desc_en": "VALVE ASSY LARGE", "sap_code": "F0100F002"}],
              "ci_lines": [{"description": "VALVE ASSY S"}, {"description": "VALVE ASSY L"}]}
        D.hynix_merge_cn(ex)
        self.assertEqual([x.get("sap_code") for x in ex["ci_lines"]], ["F0100F001", "F0100F002"])


class 해상_품명_상용구(unittest.TestCase):
    def test_docagent_수량_낱말도_뗀다(self):
        for head in ("4 DRUMS OF ", "12 BARRELS OF ", "3 PAILS OF ", "10 PCS OF ", "2 SKIDS OF "):
            with self.subTest(head=head):
                self.assertEqual(D.clean_sea_desc(head + "PHOTO RESIST"), "PHOTO RESIST")


class 엑셀_품목_짝짓기(unittest.TestCase):
    def test_포장명세서에_코드가_없어도_인보이스_PO_품목번호로_잇는다(self):
        """이관 점검에서 찾은 것. 앞 판은 이 경우 품목 줄이 두 배(인보이스 3 + 짝 없는 포장명세서 3)가 됐다."""
        import gerp_xlsx
        ci = [{"seq": i, "invoice_no": "INV-1", "po_no": "4500000001", "po_item_no": f"000{i}0",
               "sap_code": f"F0100F00{i}", "description": f"PART {i}", "qty": i, "amount": 10.0 * i} for i in (1, 2, 3)]
        pl = [{"seq": i, "invoice_no": "INV-1", "po_no": "4500000001", "po_item_no": f"000{i}0",
               "description": f"PART {i}", "qty": i, "net": 1.5 * i, "gross": 2.0 * i} for i in (3, 1, 2)]
        rows = gerp_xlsx.item_rows("c", {"ci_lines": ci, "pl_lines": pl})
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["순중량(kg)"] for r in rows], [1.5, 3.0, 4.5])
    def test_포장명세서_줄에_인보이스_번호가_없어도_한_장이면_잇는다(self):
        import gerp_xlsx
        ci = [{"seq": i, "invoice_no": "INV-1", "po_no": "4500000001", "po_item_no": f"000{i}0",
               "sap_code": f"F0100F00{i}", "description": f"PART {i}"} for i in (1, 2)]
        pl = [{"seq": i, "invoice_no": None, "po_no": "4500000001", "po_item_no": f"000{i}0",
               "description": f"PART {i}", "net": 1.0 * i} for i in (1, 2)]
        self.assertEqual(len(gerp_xlsx.item_rows("c", {"ci_lines": ci, "pl_lines": pl})), 2)

    def test_인보이스가_여러_장이면_번호_없는_줄을_함부로_잇지_않는다(self):
        import gerp_xlsx
        ci = [{"seq": 1, "invoice_no": "INV-1", "po_no": "4500000001", "po_item_no": "00010", "sap_code": "F0100F001"},
              {"seq": 2, "invoice_no": "INV-2", "po_no": "4500000001", "po_item_no": "00010", "sap_code": "F0100F002"}]
        pl = [{"seq": 1, "invoice_no": None, "po_no": "4500000001", "po_item_no": "00010", "net": 1.0}]
        self.assertEqual(len(gerp_xlsx.item_rows("c", {"ci_lines": ci, "pl_lines": pl})), 3)


if __name__ == "__main__":
    unittest.main()
