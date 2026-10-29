# -*- coding: utf-8 -*-
"""양식이 고정된 AWB 파서를 잠근다. 실제 서류는 저장소에 없으므로 같은 자리에 글을 찍은 가짜 PDF 로 잰다."""
import importlib.util, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path: sys.path.insert(0, TOOLS)
try:
    import pymupdf
    spec = importlib.util.spec_from_file_location("awb_forms", os.path.join(TOOLS, "awb_forms.py"))
    af = importlib.util.module_from_spec(spec); spec.loader.exec_module(af); HAVE = True
except Exception:
    HAVE = False


def lotte_page():
    """롯데 양식의 라벨과 값을 실측 좌표에 찍는다."""
    doc = pymupdf.open(); pg = doc.new_page(width=595, height=842)
    put = lambda x, y, t: pg.insert_text((x, y + 8), t, fontsize=7)
    put(478, 34, "HDLCA999999")
    put(24, 50, "SHIPPER'S NAME AND ADDRESS"); put(295, 50, "Not negotiable House Air Waybill")
    put(24, 62, "SAMPLE SHIPPER CO., LTD."); put(24, 74, "1, TEST-RO, SUWON, KOREA")
    put(300, 80, "11FL, YONSEI BUILDING")
    put(24, 118, "CONSIGNEE'S NAME AND ADDRESS"); put(295, 118, "Copie 1, 2 and 3")
    put(24, 130, "SAMPLE CONSIGNEE (CHINA) LTD."); put(24, 142, "LOT K7, WUXI"); put(295, 134, "MAWB NO : 99912345675")
    put(24, 186, "ISSUING CARRIER'S AGENT NAME AND CITY"); put(438, 186, "ACCOUNTING INFORMATION")
    put(59, 243, "AGENT'S IATA CODE"); put(201, 243, "ACCOUNT NO."); put(62, 256, "17-3 0413/001 2")
    put(24, 266, "AIRPORT OF DEPARTURE"); put(24, 278, "INCHEON, KOREA")
    put(23, 287, "to"); put(52, 287, "By first Carrier")
    put(327, 295, "Code PPD COLL")
    put(22, 304, "PVG"); put(51, 304, "KE"); put(294, 304, "KRW"); put(327, 304, "CP"); put(423, 304, "N.V.D"); put(496, 304, "FOBUSD 1,234.50")
    put(24, 318, "Airport of Destination"); put(161, 318, "Flight/Date")
    put(22, 333, "PUDONG AIRPORT, CHINA"); put(144, 333, "KE335/24.MAR.2026")
    put(24, 344, "HANDLING INFORMATION")
    put(22, 402, "PIECES"); put(447, 398, "NATURE AND QUANTITY OF GOODS")
    put(39, 426, "4"); put(65, 426, "183.00"); put(96, 426, "K"); put(123, 426, "Q"); put(233, 426, "183.00"); put(360, 426, "AS ARRANGED"); put(428, 426, "4 PKG OF")
    put(428, 436, "ETCHER PARTS KIYO")
    put(22, 447, "PO NO:"); put(22, 459, "4353424786")
    put(428, 506, "INV NO:"); put(428, 518, "FEM20260319-01")
    put(22, 577, "DIM: 76X76X35CM=1"); put(22, 589, "DIM: 73X73X39CM=3"); put(300, 589, "\"FREIGHT COLLECT\"")
    put(33, 602, "PREPAID"); put(120, 602, "WEIGHT CHARGE")
    return pg


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class 롯데양식(unittest.TestCase):
    def test_칸을_좌표로_읽는다(self):
        out = af.parse(lotte_page())
        self.assertIsNotNone(out)
        self.assertEqual(out["hawb"], "HDLCA999999")
        self.assertEqual(out["mawb"], "999-12345675")
        self.assertEqual(out["shipper_name"], "SAMPLE SHIPPER CO., LTD.")
        self.assertEqual(out["consignee_name"], "SAMPLE CONSIGNEE (CHINA) LTD.")
        self.assertEqual(out["agent_iata_code"], "17-3 0413/001 2")
        self.assertEqual(out["origin"], "INCHEON, KOREA")
        self.assertEqual((out["destination"], out["flight1"]), ("PUDONG AIRPORT, CHINA", "KE335"))
        self.assertEqual(out["awb_declared_value"], 1234.50)
        self.assertEqual((out["awb_pieces"], out["awb_gross"], out["awb_chargeable"]), (4, 183.0, 183.0))
        self.assertEqual(out["item_desc"], "4 PKG OF ETCHER PARTS KIYO")
        self.assertEqual((out["customer_po"], out["invoice_no"]), ("4353424786", "FEM20260319-01"))
        self.assertEqual(out["dims_cm"], [[76, 76, 35, 1], [73, 73, 39, 3]])
        self.assertEqual(out["freight_term"], "COLLECT")

    def test_모르는_양식은_None(self):
        doc = pymupdf.open(); pg = doc.new_page(); pg.insert_text((50, 50), "COMMERCIAL INVOICE")
        self.assertIsNone(af.parse(pg))
