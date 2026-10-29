"""tools/doc_survey.py 잠금.

1판은 화주 수만 보고 걸렀다. 수하인과 포워더는 자료 전체에서 같은 이름이라 화주를
아무리 세도 안 걸러졌고, 실제로 메일 주소 넷과 사람 이름 열, 회사명과 주소 여덟이
결과물에 들어갔다. 아래 LEAKED 는 그 스무 개와 **생김새만 같게 지은 합성값**이다.
거르는 쪽은 메일 꼴과 법인 접미사와 주소 꼬리와 사람 이름꼴을 보므로 합성값으로도
같은 갈래를 짚는다. 실제로 새어 나온 문자열은 실명과 사내 메일이라 저장소에 넣지
않는다(`tests/test_no_real_names.py` 가 막는다). 이것이 다시 통과하면 시험이 실패한다.

나머지 둘은 밀린 글꼴 되돌리기와 양식 묶기다. 되돌리기는 숫자표를 엉뚱하게 밀지
않아야 하고, 묶기는 칸 하나 차이로 양식을 쪼개지 않아야 한다.
"""
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("doc_survey", os.path.join(ROOT, "tools", "doc_survey.py"))
survey = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(survey)
    HAVE = True
except SystemExit:          # pymupdf 가 없는 환경
    HAVE = False

AIR = ["Shipper's Name and Address", "Airport of Departure", "Gross Weight",
       "Chargeable Weight", "Nature and Quantity of Goods", "Total Amount", "Currency"]
ALT = ["Port of Loading", "Port of Discharge", "Ocean Vessel", "Place Of Receipt",
       "Marks and Numbers", "Net Weight", "Measurement"]
# 양식은 2 로, 화주는 3 으로 갈라야 두 양식이 다 화주 셋을 넘긴다.
SHIPPERS = ["ALPHA SEMI", "BETA MATERIALS", "GAMMA CHEM"]
SECRET = "SECRET CUSTOMER ONLY ONE SHIPPER"

# 1판에서 실제로 결과물에 들어갔던 문자열
LEAKED = [
    # 메일 꼴
    "hong.gildong@example.com", "chulsoo.kim@example.com", "younghee.lee@example.org",
    "ops@example.biz",
    # 사람 이름꼴
    "A.B.HONG/Gil Dong Hong", "A.B.HONG/CHUL SOO KIM", "KIM YOUNGHEE",
    # 법인 접미사
    "Alpha Semiconductor (Sample) Ltd.", "BETA GLOBAL LOGISTICS CO., LTD.",
    "GAMMA LOGISTICS(SAMPLE)CO.,LTD.", "DELTA LOGISTICS CORPORATION",
    "EPSILON NETWORKS CO.,LTD", "Alpha system ic(Sample) Co.,Ltd",
    "Zeta Continental Shipping Co., Ltd.", "ETA MERCHANT MARINE CO,.LTD",
    "Theta Express Co.,Ltd.", "Iota L&S CO., LTD.",
    # 주소 꼬리
    "11FL, SAMPLE BUILDING", "Lot K7, Sample Bonded Zone",
    "SAMPLE DISTRICT, SAMPLE CITY",
]

# 이건 반대로 살아남아야 한다
REAL_CAPTIONS = [
    "Gross Weight", "Chargeable Weight", "Nature and Quantity of Goods", "Agent's IATA Code",
    "Shipper's Name and Address", "Port of Loading", "Total Number of Containers", "Seal No",
    "Terms of delivery and payment", "No. of Pieces RCP", "Declared Value for Carriage",
    "Freight Payable At", "Place Of Receipt", "Marks and Numbers", "Net Weight", "Rate Class",
    "Handling Information", "Accounting Information", "By First Carrier", "Measurement",
]


def make_docs(root):
    import pymupdf
    for i in range(24):
        shipper = SHIPPERS[i % 3]
        case = os.path.join(root, f"CASE{i:03d}")
        os.makedirs(case)
        with open(os.path.join(case, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"BL_NO": f"CASE{i:03d}", "SHIPPER_NM": shipper}, f)
        d = pymupdf.open()
        p = d.new_page()
        y = 40
        p.insert_text((40, y), "AIR WAYBILL", fontsize=13)
        y += 24
        # 양식을 둘로 가른다. 겹치는 비율이 SAME_FORM 아래로 내려가야 다른 양식으로
        # 세므로, 칸 하나가 아니라 절반을 바꾼다.
        caps = AIR if i % 2 else ALT
        for c in caps:
            p.insert_text((40, y), c, fontsize=7)
            y += 10
            p.insert_text((48, y), shipper, fontsize=9)
            y += 16
        # 화주 한 곳에만 나오는 문자열. 절대 밖으로 나가면 안 된다.
        if i == 0:
            p.insert_text((40, y), SECRET, fontsize=9)
        d.save(os.path.join(case, f"CASE{i:03d}_HB.pdf"))
        d.close()


@unittest.skipUnless(HAVE, "pymupdf 가 없다")
class DocSurvey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = os.path.join(cls.tmp, "docs_raw")
        cls.out = os.path.join(cls.tmp, "out")
        os.makedirs(cls.src)
        make_docs(cls.src)
        argv = ["--src", cls.src, "--out", cls.out]
        buf, sys.stdout = sys.stdout, open(os.devnull, "w")
        try:
            survey.main(argv)
        finally:
            sys.stdout.close()
            sys.stdout = buf
        with open(os.path.join(cls.out, "labels.json"), encoding="utf-8") as f:
            cls.labels = json.load(f)
        with open(os.path.join(cls.out, "coverage.txt"), encoding="utf-8") as f:
            cls.coverage = f.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_화주_하나짜리_문자열은_안_나간다(self):
        blob = json.dumps(self.labels, ensure_ascii=False) + self.coverage
        self.assertNotIn(SECRET, blob)
        for shipper in SHIPPERS:
            self.assertNotIn(shipper, blob, f"화주 이름이 결과에 새어 나갔다: {shipper}")

    def test_양식_문구는_남는다(self):
        got = {x["label"] for x in self.labels["labels"]}
        for cap in AIR[:-1] + ALT[:-1]:
            self.assertIn(cap, got, f"공통 칸 이름이 사라졌다: {cap}")

    def test_남은_것은_전부_화주_기준을_넘는다(self):
        for x in self.labels["labels"]:
            self.assertGreaterEqual(x["shippers"], survey.MIN_SHIPPERS)
            self.assertGreaterEqual(x["docs"], survey.MIN_DOCS)

    def test_양식_종수는_심은_대로_둘이다(self):
        # 두 양식은 서류 종류가 갈려 서로 다른 줄에 잡힌다. 줄마다 한 종씩,
        # 합쳐서 둘이어야 한다. 칸 하나 차이로 쪼개면 여기서 늘어난다.
        got = [int(x) for x in re.findall(r"서류\s+\d+건\s+양식\s+(\d+)종", self.coverage)]
        self.assertTrue(got, self.coverage)
        self.assertEqual(sum(got), 2, self.coverage)

    def test_서류_종류를_가른다(self):
        self.assertGreaterEqual(sum(self.labels["bytype"].values()), 24)

    def test_합본은_두_종류로_센다(self):
        self.assertEqual(survey.doctypes("COMMERCIAL INVOICE\nPACKING LIST\nCBM\nUnit Price"),
                         ["CI", "PL"])

    def test_제목이_없으면_기타다(self):
        self.assertEqual(survey.doctypes("   "), ["기타"])

    def test_어느_칸도_문서_수를_넘지_않는다(self):
        """대소문자만 다른 변형을 따로 세면 어떤 칸의 건수가 문서 수를 넘는다.
        실측에서 Notify Party 가 BL 문서 146건 중 153건으로 나왔다."""
        total = self.labels["scanned"]
        for x in self.labels["labels"]:
            self.assertLessEqual(x["docs"], total, f"{x['label']} 가 문서 수를 넘는다")
            for t, n in x["types"].items():
                self.assertLessEqual(n, self.labels["bytype"][t],
                                     f"{x['label']} 의 {t} 건수가 {t} 문서 수를 넘는다")

    def test_값처럼_생긴_것은_칸_이름이_아니다(self):
        self.assertFalse(survey.is_caption("1,234.56"))
        self.assertFalse(survey.is_caption("2026-06-18"))
        self.assertTrue(survey.is_caption("Gross Weight"))

    def test_1판에서_새어_나온_것이_다시_통과하지_않는다(self):
        got = [x for x in LEAKED if survey.is_caption(x)]
        self.assertEqual(got, [], f"개인 정보나 회사명이 다시 통과한다: {got}")

    def test_진짜_칸_이름은_살아남는다(self):
        lost = [x for x in REAL_CAPTIONS if not survey.is_caption(x)]
        self.assertEqual(lost, [], f"칸 이름이 잘못 걸러졌다: {lost}")

    def test_밀린_글꼴을_되돌린다(self):
        for src, want in (
            ("3UJKR4U 3GZKXOGR (XGTJ 4KZ=KOMNZ *OYIXOVZOUT 8KSGXQ ;YK,UX " * 6, "Material"),
            ("΅ΖΣΞΤ͑ΠΗ͑ΕΖΝΚΧΖΣΪ ΁ͲʹͼͺͿ͸͑ͽͺ΄΅ ͿΠΥΚΗΪ͑ΡΒΣΥΪ ΂ΦΒΟΥΚΥΪ " * 8, "Quantity"),
            ("%QWPVT[1H1TKIKP &GUETKRVKQP 0GV9GKIJV /CVGTKCN " * 10, "ESCRIPTION"),
        ):
            got, off = survey.unshift(src)
            self.assertNotEqual(off, 0, f"되돌리지 못했다: {src[:30]}")
            self.assertIn(want, got)

    def test_멀쩡한_문서에_붙은_밀린_부속표만_되돌린다(self):
        """실측에서 밀린 것은 멀쩡한 AWB 뒤에 붙은 중국 통관 신고표뿐이었다.
        문서 전체를 한 덩어리로 재면 앞쪽 영문 때문에 못 잡는다."""
        clean = [(x, 6.0) for x in [
            "Shipper's Name and Address", "Airport of Departure", "Gross Weight",
            "Chargeable Weight", "Nature and Quantity of Goods", "Agent's IATA Code",
            "Declared Value for Carriage", "MAWB NO", "Rate Class", "Account No.",
            "WT/VAL", "RCP", "N.V.D", "PPD", "COLL", "lb", "EA", "Q'TY", "NIL"]]
        annex = [(x, 6.0) for x in [
            "3UJKR4U", "3GZKXOGR", "(XGTJ", "4KZ=KOMNZ", "*OYIXOVZOUT", "1K_6GXGSKZKXY",
            "8KSGXQ", ";YK,UX", ")NKSOIGR", "+W[OVSKTZ3UJKR4U", ")U[TZX_5L5XOMOT"]]
        out, off = survey.unshift_lines(clean + annex)
        self.assertEqual(off, 26)
        kept = [t for t, _ in out[:len(clean)]]
        self.assertEqual(kept, [t for t, _ in clean], "멀쩡한 줄을 건드렸다")
        got = [t for t, _ in out[len(clean):]]
        for want in ("Material", "Brand", "NetWeight", "Chemical", "CountryOfOrigin"):
            self.assertIn(want, got, f"되돌리지 못했다: {want}")

    def test_멀쩡한_글은_안_건드린다(self):
        for src in ("Shipper Name and Address  Consignee  Port of Loading  Gross Weight  "
                    "Description of Goods  Total Amount  Invoice No  Date " * 6,
                    "적재항 및 출항예정일 총중량 순중량 용적 송하인 수하인 화물표시 접수번호 " * 8,
                    "1 2 3 4 5 6 7 8 9 0 1234 5678 90.12 34.56 78.90 " * 20):
            got, off = survey.unshift(src)
            self.assertEqual(off, 0, f"멀쩡한 글을 밀었다: {src[:30]}")
            self.assertEqual(got, src)

    def test_칸_하나_차이로_양식을_쪼개지_않는다(self):
        base = {chr(ord("a") + i) for i in range(20)}
        idx, n = survey.families([base, base | {"z"}, base - {"a"}, {"1", "2", "3"}])
        self.assertEqual(idx[:3], [0, 0, 0], "칸 한둘 차이인데 다른 양식으로 갈랐다")
        self.assertEqual(n, 2)


if __name__ == "__main__":
    unittest.main()
