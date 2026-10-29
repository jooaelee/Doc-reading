# -*- coding: utf-8 -*-
"""SO 업로드 양식이 60열 그대로이고, FAIL 이 있는 행이 표시되는지 잠근다."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
try:
    spec = importlib.util.spec_from_file_location("so_upload", os.path.join(TOOLS, "so_upload.py"))
    su = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(su)
    import openpyxl
    HAVE = True
except Exception:
    HAVE = False


@unittest.skipUnless(HAVE, "openpyxl 이 없습니다")
class 양식(unittest.TestCase):
    def test_60열과_검토_시트(self):
        d = tempfile.mkdtemp(prefix="so-")
        recs = [
            {"case": "C1", "raw": {"hawb": "H1", "mawb": "999-12345675", "ci_total": 10, "currency": "USD"},
             "verify": [("OK", "hawb", "x")], "llm": {"wall": 12000}},
            {"case": "C2", "raw": {"hawb": "H2", "ci_total": 5},
             "verify": [("FAIL", "ci_total", "x"), ("WARN", "chargeable", "y")], "llm": {"wall": 8000}},
            {"case": "C3", "error": "죽었습니다"},
        ]
        for r in recs:
            with open(os.path.join(d, r["case"] + ".json"), "w", encoding="utf-8") as f:
                json.dump(r, f)
        out = os.path.join(d, "so.xlsx")
        s = su.write_xlsx(su.load_preds(d), out)
        self.assertEqual((s["rows"], s["clean"], s["flagged"], s["failed"]), (2, 1, 1, 1))
        wb = openpyxl.load_workbook(out)
        ws = wb["SO"]
        self.assertEqual([c.value for c in ws[1]], su.docpipe.SO_COLS)
        self.assertEqual(ws.max_row, 3)
        self.assertEqual(ws.cell(2, su.docpipe.SO_COLS.index("HBL No") + 1).value, "H1")
        self.assertEqual(ws.cell(2, su.docpipe.SO_COLS.index("MBL No") + 1).value, "99912345675")
        rv = wb["검토"]
        self.assertEqual([c.value for c in rv[1]], su.REVIEW_COLS)
        self.assertEqual(rv.cell(3, 2).value, 1)      # C2 FAIL 1
        self.assertIn("죽었습니다", rv.cell(4, 9).value)


@unittest.skipUnless(HAVE, "openpyxl 이 없습니다")
class 두바퀴(unittest.TestCase):
    def test_갈린_SO_칸을_검토_시트에_적는다(self):
        a = {"case": "C1", "raw": {"hawb": "H1", "ci_total": 10}, "verify": [], "llm": {"wall": 1000}}
        b = {"case": "C1", "raw": {"hawb": "H1", "ci_total": 11}, "verify": [], "llm": {"wall": 1000}}
        self.assertEqual(su.differing(a, b), ["ci_total"])
        self.assertEqual(su.differing(a, a), [])
        self.assertEqual(su.differing(a, None), [])
        d = tempfile.mkdtemp(prefix="so-")
        with open(os.path.join(d, "C1.json"), "w", encoding="utf-8") as f: json.dump(a, f)
        out = os.path.join(d, "so.xlsx")
        s = su.write_xlsx(su.load_preds(d), out, {"C1": b})
        self.assertEqual((s["flagged"], s["differ"]), (1, 1))
        rv = openpyxl.load_workbook(out)["검토"]
        self.assertEqual(rv.cell(2, 10).value, "ci_total")
