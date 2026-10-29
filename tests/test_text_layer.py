# -*- coding: utf-8 -*-
"""글자층 보정을 잠근다.

밀린 글꼴 쪽을 되돌리지 않고 그림으로 읽는 이유가 여기 있다. 같은 쪽 안에서 글꼴마다
밀린 폭이 다르고, 숫자는 제어 문자 자리로 밀려 글자층에서 사라진다. 되돌린 글에는
금액이 없고, 모델은 그 자리를 다른 서류에서 지어냈다.
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


def shift(t, off):
    return "".join(chr(ord(c) + off) if c.isalpha() else c for c in t)


GOOD = "\n".join(["Commercial Invoice", "Company WOOSUNG", "Address KEUNMAL-GIL OSAN",
                  "Invoice No WS-001", "Contact person LEE", "Port of Loading INCHEON",
                  "Total Packages 2", "Description H2 DETECTOR", "Currency USD"])


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 밀린글꼴(unittest.TestCase):
    def test_밀린_쪽은_그림으로_읽는다(self):
        p = dp.Page("a.pdf", 1, shift(GOOD, -29), 0)
        p.garbled = dp.is_garbled(p.text)
        self.assertTrue(p.garbled)
        dp.classify(p)
        self.assertTrue(p.needs_image)

    def test_크게_밀려_ASCII_밖으로_나간_쪽도_잡는다(self):
        # 실측 -817. 라틴 글자가 그리스 글자 자리로 간다. 자모 수로 고르면 이 줄들을 아예 안 본다.
        p = dp.Page("a.pdf", 1, shift(GOOD, 817), 0)
        self.assertTrue(dp.is_garbled(p.text))

    def test_멀쩡한_쪽은_그대로_둔다(self):
        p = dp.Page("a.pdf", 1, GOOD, 0)
        self.assertFalse(dp.is_garbled(p.text))
        self.assertNotEqual(dp.classify(p).kind, "SCAN")

    def test_짧은_쪽은_판단하지_않는다(self):
        self.assertFalse(dp.is_garbled(shift("Company\nAddress", 29)))


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 전각과정렬(unittest.TestCase):
    def test_전각을_민다(self):
        self.assertEqual(dp.fix_text("ＮＥＣ ４６１６"), "NEC 4616")

    def test_AWB_쪽만_정렬본으로_바꾼다(self):
        a = dp.Page("a.pdf", 1, "x", 0, kind="AWB", sorted_text="sorted")
        b = dp.Page("a.pdf", 2, "y", 0, kind="CI", sorted_text="sorted")
        dp.apply_awb_sort([a, b])
        self.assertEqual((a.text, b.text), ("sorted", "y"))

    def test_끄면_안_바꾼다(self):
        old = dict(dp.TEXT_FIX)
        dp.TEXT_FIX["awb_sort"] = False
        try:
            a = dp.Page("a.pdf", 1, "x", 0, kind="AWB", sorted_text="sorted")
            dp.apply_awb_sort([a])
            self.assertEqual(a.text, "x")
        finally:
            dp.TEXT_FIX.update(old)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 스캔그림(unittest.TestCase):
    def test_스캔_쪽만_PNG_로_그린다(self):
        import tempfile
        try:
            import pymupdf
        except ImportError:
            self.skipTest("pymupdf 가 없습니다")
        pdf = os.path.join(tempfile.mkdtemp(prefix="dp-"), "a.pdf")
        d = pymupdf.open(); d.new_page().insert_text((60, 80), "AIR WAYBILL"); d.new_page(); d.save(pdf)
        pages = dp.load_pdf(pdf)
        pages[1].kind = "SCAN"; pages[0].kind = "AWB"
        out = dp.scan_images(pages, tempfile.mkdtemp(prefix="img-"))
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].endswith("page01.png"))
        self.assertTrue(os.path.getsize(out[0]) > 100)

    def test_하우스_번호의_공백은_후처리가_뺀다(self):
        self.assertEqual(dp.postprocess({"hawb": "NEC 4616 6105"})["hawb"], "NEC46166105")


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 숫자줄(unittest.TestCase):
    def test_숫자와_기호_줄이_많아도_밀린_쪽을_잡는다(self):
        # 케이스 05. 값 줄은 '$7(' 나 321R 처럼 자모가 넷 미만이라 분모에 넣으면 비율이 떨어진다.
        t = shift(GOOD, 29) + "\n" + "\n".join(["'$7('", "321R", "12", "77", "3,000", "(0)", "-", "9", "1/1", "2026"] * 3)
        self.assertTrue(dp.is_garbled(t))


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 밀린쪽의종류(unittest.TestCase):
    CI = "\n".join(["Commercial Invoice", "Invoice No WS-001", "Unit Price 12.00", "Amount 24.00", "Quantity 2",
                    "Description H2 DETECTOR", "Total Amount 24.00", "Sold to SK HYNIX", "Payment Term T/T"] * 2)
    CUSTOMS = "\n".join(["Seq Po No. SKhynix systemic code Discription", "Chinese Name Goods Photo Brand Maker",
                         "Model/Specification Material Use For Function", "Country Of Origin Export declaration HS code",
                         "New,old or Repair Remark", "Import Goods Information For China Customs Clearance",
                         "Hydrogen Gas Detector Model Stainless Steel", "Electrochemical Sensor Unit",
                         "Detects and monitors hydrogen concentration"] * 2)

    def test_밀린_인보이스는_CI_힌트로_그림으로_읽는다(self):
        p = dp.Page("a.pdf", 1, shift(self.CI, -29), 0)
        p.garbled = dp.is_garbled(p.text)
        self.assertTrue(p.garbled)
        dp.classify(p)
        self.assertEqual(p.kind, "CI")
        self.assertTrue(p.needs_image)
        self.assertIn("[scanned page, no text layer]", dp.build_messages([p])[1]["content"])
        self.assertEqual([k for k, _ in dp.split_groups([p])], ["CI"])

    def test_밀린_통관표는_버린다(self):
        p = dp.Page("a.pdf", 1, shift(self.CUSTOMS, -29), 0)
        p.garbled = dp.is_garbled(p.text)
        self.assertTrue(p.garbled)
        dp.classify(p)
        self.assertEqual(p.kind, "CUSTOMS")
        self.assertEqual(dp.select_pages([p]), [])

    def test_밀린_뒷장은_앞_서류에_붙는다(self):
        # 인보이스 둘째 장은 줄 항목뿐이라 낱말이 없다. 버리면 총계를 잃는다.
        a = dp.Page("a.pdf", 1, shift(self.CI, -29), 0)
        b = dp.Page("a.pdf", 2, shift("\n".join(["11 EBZ00001 Gas Detector 5 EA 247.00 1235.00"] * 8 + ["TOTAL 188 56,199.00 USD"]), -29), 0)
        for p in (a, b):
            p.garbled = dp.is_garbled(p.text); dp.classify(p)
        self.assertEqual(b.kind, "OTHER")
        g = dp.split_groups([a, b])
        self.assertEqual([(k, len(pg)) for k, pg in g], [("CI", 2)])

    def test_인코텀즈는_세_글자만_남긴다(self):
        self.assertEqual(dp.postprocess({"incoterms": "F.O.B KOREA"})["incoterms"], "FOB")
        self.assertEqual(dp.postprocess({"incoterms": "EXW, WUXI SKHYCL"})["incoterms"], "EXW")
        self.assertEqual(dp.postprocess({"incoterms": "DAP"})["incoterms"], "DAP")


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 제어문자(unittest.TestCase):
    def test_널_바이트를_뺀다(self):
        self.assertEqual(dp.fix_text("AIR\x00 WAYBILL\x07\n123"), "AIR WAYBILL\n123")
