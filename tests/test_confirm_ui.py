# -*- coding: utf-8 -*-
"""확인 화면 생성기가 두 파일을 내고 값을 담는지 잠근다."""
import importlib.util, json, os, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path: sys.path.insert(0, TOOLS)
try:
    spec = importlib.util.spec_from_file_location("confirm_ui", os.path.join(TOOLS, "confirm_ui.py"))
    cu = importlib.util.module_from_spec(spec); spec.loader.exec_module(cu); HAVE = True
except Exception:
    HAVE = False


@unittest.skipUnless(HAVE, "docpipe 가 안 올라옵니다")
class 생성(unittest.TestCase):
    def test_두_화면을_내고_케이스와_SO_행을_담는다(self):
        d = tempfile.mkdtemp(prefix="ui-"); pred = os.path.join(d, "pred"); os.makedirs(pred)
        rec = {"case": "C1", "raw": {"hawb": "H1", "ci_total": 10, "currency": "USD"}, "verify": [("FAIL", "ci_total", "x")],
               "llm": {"wall": 1000, "forms": "LOTTE"}, "wall": 12.3}
        with open(os.path.join(pred, "C1.json"), "w", encoding="utf-8") as f: json.dump(rec, f)
        out = os.path.join(d, "ui")
        n = cu.write_ui(cu.load_preds(pred), {}, "", out)
        self.assertEqual(n, 1)
        html = open(os.path.join(out, "confirm.html"), encoding="utf-8").read()
        self.assertIn('"case": "C1"', html); self.assertIn("HBL No", html); self.assertIn("KEROL 등록", html)
        self.assertIn("서류 등록", html); self.assertIn("pdf.min.js", html)
        for noisy in ("원본과 견줍니다", "노란 자리가", "폭 맞춤", "뽑기를 누르십시오"):
            self.assertNotIn(noisy, html)
        self.assertNotIn("—", html)
        k = open(os.path.join(out, "kerol_mock.html"), encoding="utf-8").read()
        self.assertIn("HAWB List (In)", k)

    def test_값의_자리를_PDF_에서_찾는다(self):
        import pymupdf
        d = tempfile.mkdtemp(prefix="hl-"); pdf = os.path.join(d, "a.pdf")
        doc = pymupdf.open(); pg = doc.new_page(); pg.insert_text((50, 80), "MAWB NO : 99912345675"); pg.insert_text((50, 120), "TOTAL 1,148.92 USD"); doc.save(pdf)
        hl = cu.highlights([pdf], {"mawb": "999-12345675", "ci_total": 1148.92, "hawb": None})
        self.assertIn("mawb", hl); self.assertIn("ci_total", hl); self.assertNotIn("hawb", hl)
        self.assertEqual(hl["mawb"][0][:2], [0, 1])
