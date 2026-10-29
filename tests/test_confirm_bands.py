# -*- coding: utf-8 -*-
"""확인 화면의 줄 띠 짚기(confirm_ui.line_bands) 잠금. 서류 없이 글 토막 좌표로 시험한다."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import confirm_ui as cu  # noqa: E402


def tok(t, x, y, w=30, h=6):
    return (cu._norm(t), x, y, x + w, y + h)


class LineBandsTest(unittest.TestCase):
    def setUp(self):
        # 한 쪽에 같은 품목표가 두 번 실린다. 위 표(견적가 30,000)와 아래 표(실제 청구 18,000)
        head = [tok("Qty", 300, 90), tok("Unit price", 340, 90, 50), tok("Amount", 420, 90)]
        rows = []
        for i, sn in enumerate(["SN0001", "SN0002", "SN0003"]):
            yu, yl = 100 + i * 12, 200 + i * 12
            rows += [tok("P3192754", 20, yu), tok(sn, 120, yu), tok("1", 305, yu, 4), tok("30,000.00", 340, yu),
                     tok("30,000.00", 420, yu)]
            rows += [tok("P3192754", 20, yl), tok(sn, 120, yl), tok("1", 305, yl, 4), tok("18,000.00", 340, yl),
                     tok("18,000.00", 420, yl)]
        self.tokens = {(0, 3): head + rows}
        self.kinds = [["AWB", "AWB", "CI"]]
        self.lex, self.hl, self.rows = {}, {}, []
        for i, sn in enumerate(["SN0001", "SN0002", "SN0003"]):
            self.rows.append({"출처": "CI"})
            self.lex.update({f"L{i}:Material No": "P3192754", f"L{i}:SERIAL NUMBER": sn,
                             f"L{i}:QUANTITY": 1, f"L{i}:UNIT PRICE": 18000.0, f"L{i}:AMOUNT": 18000.0})
            # 통째 찾기가 남긴 자리. 시리얼은 위아래 표 두 곳에 걸린다
            self.hl[f"L{i}:SERIAL NUMBER"] = [[0, 3, 120, 100 + i * 12, 150, 106 + i * 12],
                                               [0, 3, 120, 200 + i * 12, 150, 206 + i * 12]]

    def run_bands(self):
        alt = []
        cu.line_bands(self.lex, self.hl, self.tokens, self.rows, self.kinds, alt)
        return alt

    def test_reads_the_priced_table(self):
        self.run_bands()
        for i in range(3):
            y = 200 + i * 12
            for c in ("Material No", "SERIAL NUMBER", "UNIT PRICE", "AMOUNT", "QUANTITY"):
                h = self.hl.get(f"L{i}:{c}")
                self.assertTrue(h, f"L{i}:{c} 를 못 짚었다")
                self.assertEqual(h[0][3], y, f"L{i}:{c} 가 다른 줄을 짚었다")

    def test_price_and_amount_get_their_own_cells(self):
        self.run_bands()
        for i in range(3):
            self.assertEqual(self.hl[f"L{i}:UNIT PRICE"][0][2], 340)
            self.assertEqual(self.hl[f"L{i}:AMOUNT"][0][2], 420)

    def test_quote_table_is_dashed(self):
        alt = self.run_bands()
        self.assertTrue(alt, "견적 표를 점선으로 짚지 않았다")
        self.assertTrue(all(a[3] < 140 for a in alt), "점선이 청구 표에 섞였다")
        self.assertFalse(any(a[2] in (340, 420) for a in alt), "값이 다른 견적가 칸에 점선이 생겼다")

    def test_no_dashed_box_without_a_repeat(self):
        # 아래 표만 있으면 점선이 없어야 한다
        self.tokens[(0, 3)] = [t for t in self.tokens[(0, 3)] if t[2] >= 150 or t[2] < 95]
        for i in range(3):
            self.hl[f"L{i}:SERIAL NUMBER"] = self.hl[f"L{i}:SERIAL NUMBER"][1:]
        self.assertEqual(self.run_bands(), [])


    def test_verified_quantity_gets_a_cell_even_if_ocr_misses_it(self):
        # 둘째 줄 수량 글자를 OCR 이 못 읽었다. 둘째 줄이 검산(수량 × 단가 = 금액)을 통과했으면 열 자리로 짚는다
        self.tokens[(0, 3)] = [t for t in self.tokens[(0, 3)] if not (t[0] == "1" and t[2] == 212)]
        cu.line_bands(self.lex, self.hl, self.tokens, self.rows, self.kinds, [], None, {1, 2, 3})
        h = self.hl.get("L1:QUANTITY")
        self.assertTrue(h)
        self.assertEqual(round((h[0][3] + h[0][5]) / 2), 215)
        self.assertEqual(h[0][2], 305)

    def test_unverified_quantity_is_left_empty(self):
        self.tokens[(0, 3)] = [t for t in self.tokens[(0, 3)] if not (t[0] == "1" and t[2] == 212)]
        cu.line_bands(self.lex, self.hl, self.tokens, self.rows, self.kinds, [], None, {1, 3})
        self.assertFalse(self.hl.get("L1:QUANTITY"))


if __name__ == "__main__":
    unittest.main()
