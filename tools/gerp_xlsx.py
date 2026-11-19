# -*- coding: utf-8 -*-
"""하이닉스 판(h1) 추출 결과를 GERP 품목 표 모양의 엑셀로 낸다.

    python3 tools/docpipe.py --bundle 케이스=a.pdf --prompt h1 --claude-cli --save out.json
    python3 tools/gerp_xlsx.py --pred out.json --out work/out/gerp.xlsx

시트
  BL Header   선적 한 건의 헤더. GERP 「Change Bill of Lading: General1 (China)」 윗부분.
  BL Item     품목 줄. 같은 화면 아랫부분의 B/L Item Update 표.
  인보이스    묶음에 들어온 인보이스마다 번호와 총액. 여러 장이면 여기서 갈린다.
  핵심 3항목  유상 품목에 사람이 적는 셋(PO, Material, 수량)이 줄마다 찼는지.
  Main 항목   고객 추출 대상(PO No., Material Code, 품명, 부품 번호, 납품 수량, 단위, 단가). 아래 ITEM_SPEC.
  Sub 항목    그 밖에 읽는 것(금액, 시리얼 번호, 원산지, 중량, NCV 단서 ...).
  빈칸 진단   항목마다 찬 줄 수. 정확도가 아니라 빈칸 까닭을 가르는 데 쓴다.
  항목 정의   Main 과 Sub 항목의 뜻, 견주는 법.
  검토        케이스마다 FAIL 과 WARN, PO 나 품목 코드가 빈 줄 수.
  읽는 법     어느 열이 서류에서 오고 어느 열이 GERP 에서 오는지, 그리고 잇는 규칙.

🔴 왜 이 모양인가. 받는 쪽이 이 화면에 넣는다. 우리 SO 업로드 양식(60열)은 KEROL 이
   받는 모양이고 하이닉스는 KEROL 을 안 쓴다. 같은 추출 결과라도 표 모양이 다르면
   사람이 손으로 옮긴다. 그러면 자동화한 자리가 도로 사람 손으로 온다.

🔴 잇는 규칙을 한 곳에 모은다. 서류에 인쇄된 코드 열이 다섯이다(Sap Code, Po No.,
   Po Item No., SK Hynix I/V No., HIOS No.). 추출은 인쇄된 이름 그대로 받고, GERP 열로
   잇는 일은 GERP_MAP 한 자리에서 한다. 다섯 열을 표에 그대로 같이 실어서 상대가 눈으로
   확인할 수 있게 한다. Material 만은 열 이름이 아니라 하이닉스 자재 번호 형식으로 고른다
   (hynix_material. 고객 PPT ’26.11.04 의 SAP 화면에서 Material 칸에 들어간 값이 그 형식이다).

결과 파일에는 서류 값이 들어가므로 저장소 밖에 둔다.
"""
import sys

if sys.version_info < (3, 7):
    sys.exit("파이썬 3.7 이상이 필요합니다.")

import argparse, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import docpipe

# 🔴 판을 먼저 고른다. 안 고르면 기본 판(v3)의 스키마로 후처리가 돌고, 그 판에 없는 줄 칸
#    (unit, country_of_origin, sap_code ...)이 글자가 아닌 것으로 취급돼 숫자 변환에 날아간다.
#    실제로 UNIT 이 44줄 중 0줄, 원산지가 40줄에서 24줄로 줄어든 채 엑셀이 나왔다.
docpipe.set_prompt("h1")

# GERP 열 → 추출 칸. 앞에 있는 것부터 찬 것을 쓴다. 빈 튜플은 GERP 쪽에서 채우는 열이다.
GERP_MAP = [
    ("Imp.Req. No.", ()),
    ("Item", ("po_item_no",)),
    ("Purchase Order", ("po_no",)),
    ("Material", ("sap_code", "material_code")),
    ("Vendor Part No.", ("hios_no", "part_no")),
    ("B/L quantity", ("qty",)),
    ("Invoice amount", ("amount",)),
    ("Net Price", ("unit_price",)),
]
GERP_ITEM_COLS = [c for c, _ in GERP_MAP]
GERP_ONLY = [c for c, src in GERP_MAP if not src]          # 서류에 없는 열
MAP_KEY = {c: src for c, src in GERP_MAP}
# 서류에 인쇄된 코드 열 전부. 상대가 GERP_MAP 을 확인하는 자리다.
CODE_COLS = [("Sap Code", "sap_code"), ("Po Item No.", "po_item_no"),
             ("SK Hynix I/V No.", "iv_no"), ("HIOS No.", "hios_no"),
             ("Material Code", "material_code"), ("Serial No.", "serial_no"),
             ("Part No.", "part_no")]
# 🔴 Currency 를 Net Price 바로 뒤에 둔다(11/10 임지원 PM). 화면에서는 케이스 열이 숨으므로
#    「Net Price, Currency, Invoice No.」 로 붙어 보인다. 통화는 인보이스 단위 값이라 줄마다 그 인보이스의 통화를 잇는다
# 🔴 BL No. 는 서류에서 읽지 않고 올린 PDF 파일 이름에서 넣는다(11/19). 우시는 파일 이름이 HBL 번호라 서류에서
#    찾는 것보다 정확하다. 값은 wuxi_engine.payload 가 채우고(bl_no_of_file), 화면에서는 고치지 않는다.
#    「Net Price, Currency, Invoice No.」 가 붙어 보이는 차례(위)를 지키려고 Invoice No. 뒤에 둔다
EXTRA_ITEM_COLS = (["Currency", "케이스", "Invoice No.", "BL No.", "출처", "줄", "품명", "단위"]
                   + [c for c, _ in CODE_COLS]
                   + ["Case No.", "순중량(kg)", "총중량(kg)", "HS", "원산지", "NCV 단서"])
ITEM_COLS = GERP_ITEM_COLS + EXTRA_ITEM_COLS

# 🔴 항목 정의(’26.11.10 다시 정함). 하이닉스 우시가 준 「BL Item AI판독 주의 사항」(’26.11.04)의
#    추출 대상을 Main 으로 둔다. 「품명/모델」 은 정확도를 따로 재려고 품명과 부품 번호 두 칸으로 나눈다.
#    우리가 읽어 오던 나머지는 Sub 다. 9/30 메일의 열 개는 이 둘에 모두 들어 있다(Material No 와
#    SK HYNIX Sap Code 는 같은 하이닉스 자재 번호라 Material Code 하나가 됐다).
#    1차는 유상과 무상을 가르지 않고 다 뽑는다. 무상 품목에 PO 와 Material Code 가 없는 것은 정상이다.
# 🔴 채움률은 목표가 아니다. 칸이 찼는지만 보므로 틀린 값도 성공으로 센다. 목표는 사람이 확정한 정답과
#    견준 정확도이고, 원본에 없는 값은 빈칸이 정답이다. 채움률(「빈칸 진단」 시트)은 빈칸이 왜 생겼는지
#    가르는 데만 쓴다.
# 🔴 납품 수량은 1차에서 인보이스에 인쇄된 수량이다. PPT 의 단위 환산(드럼을 kg 으로), 같은 PO 같은 품목
#    줄 합치기, 장비 수량 환산은 SAP 입력 규칙이라 판독 정확도와 따로 다룬다.
#   (등급, 이름, BL Item 행의 열, 견주는 법, 비면 노랗게, 설명)
#   견주는 법: code 는 띄어쓰기와 대소문자 무시, number 는 숫자로, unit 은 값 바꾸기 규칙을 건 뒤, text 는 기호 무시
ITEM_SPEC = [
    ("Main", "BL No.", "BL No.", "file", False,
     "HBL 번호. 서류에서 읽지 않고 올린 PDF 파일 이름(확장자를 빼고 첫 낱말)을 넣음. 파일이 여럿이면 그 줄을 읽은 파일"),
    ("Main", "PO No.", "Purchase Order", "code", True, "인보이스 줄의 PO 번호. 무상 품목은 없을 수 있음"),
    ("Main", "Material Code", "Material", "code", False,
     "하이닉스 SAP 자재 번호(예 R1257986). 공급사가 Sap Code 열이나 Material 열에 찍고, 통관표의 "
     "SKhynix SAP Code 열에도 찍힘. 공급사 자체 자재 번호는 넣지 않음. 무상 품목은 없을 수 있음"),
    ("Main", "품명", "품명", "text", True, "인보이스 줄의 품명"),
    ("Main", "부품 번호", "Vendor Part No.", "code", False, "모델이나 공급사 부품 번호. HIOS No. 열이 있으면 그 값"),
    ("Main", "납품 수량", "B/L quantity", "number", True, "인보이스에 인쇄된 수량. 단위 환산과 줄 합치기는 하지 않음"),
    ("Main", "단위", "단위", "unit", False, "인보이스 줄의 수량 단위"),
    ("Main", "단가", "Net Price", "number", True, "인보이스 줄의 단가"),
    ("Sub", "금액", "Invoice amount", "number", True, "인보이스 줄의 금액. 빠짐 검사(금액 합)에 씀"),
    ("Sub", "통화", "Currency", "code", True, "그 줄이 속한 인보이스의 통화(USD, JPY ...). 인보이스 총액 옆에 찍힌 값"),
    ("Sub", "PO 품목 번호", "Item", "code", False, "인보이스의 Po Item No. 열"),
    ("Sub", "시리얼 번호", "Serial No.", "code", False, "인보이스의 Serial No. 열. 그 열이 있는 서류만"),
    ("Sub", "원산지", "원산지", "unit", False, "줄이나 서류 머리에 찍힌 원산지"),
    ("Sub", "HS 코드", "HS", "code", False, "인보이스 줄의 HS 코드"),
    ("Sub", "케이스 번호", "Case No.", "code", False, "포장명세서 줄에서 이어 붙임"),
    ("Sub", "순중량(kg)", "순중량(kg)", "number", False, "포장명세서 줄에서 이어 붙임"),
    ("Sub", "총중량(kg)", "총중량(kg)", "number", False, "포장명세서 줄에서 이어 붙임"),
    ("Sub", "NCV 단서", "NCV 단서", "text", False,
     "무상 품목으로 볼 근거(N.C.V., FREE OF CHARGE 같은 문구, 단가 0). 유무상을 판정하지 않고 근거만 적음"),
]
MAIN_ITEMS = [s for s in ITEM_SPEC if s[0] == "Main"]
SUB_ITEMS = [s for s in ITEM_SPEC if s[0] == "Sub"]
ITEM_COL = {s[1]: s[2] for s in ITEM_SPEC}           # 항목 이름 → BL Item 행의 열
# 🔴 서류에 그 열이 인쇄돼 있는지 보는 낱말. 「우리가 못 읽은 것」 과 「서류에 없는 것」 을
#    가르는 데 쓴다. 둘을 같은 분모에 넣으면 시리얼 번호가 15.9% 로 보인다. 실제로는
#    그 열이 있는 서류에서는 전부 뽑았고, 일곱 건 중 한 건에만 그 열이 있었다.
REQ_HEAD = {
    "PO No.": r"(?i)p\s*/?\s*o\.?\s*(no|number)|purchase\s*order|\bpo\s*no\b",
    "Material Code": r"(?i)\bmaterial\s*(no|code|number)?\b|sap\s*code|物料代码",
    "품명": r"(?i)description|commodity|item\s*name|品名",
    "납품 수량": r"(?i)\bq[\'’]?ty\b|quantity|수량|数量",
    "단위": r"(?i)\bunit\b|\bea\b|\bpcs\b|\bset\b|\bqty\s*/\s*unit\b",
    "단가": r"(?i)unit\s*price|단가",
    "금액": r"(?i)\bamount\b|금액",
    "시리얼 번호": r"(?i)serial\s*(no|number)|\bs\s*/\s*n\b|序列号",
    "원산지": r"(?i)origin|원산지|原产国|made\s+in",
    "INCOTERMS": r"(?i)incoterm|\bfob\b|\bexw\b|\bcif\b|\bddp\b|\bdap\b|\bcfr\b",
}


def bl_no_of_file(name: str) -> str:
    """PDF 파일 이름에서 BL No. 를 낸다. 확장자를 빼고 첫 낱말이다(「HDLCA125978.pdf」 는 HDLCA125978,
    「SFSA2610153  正式件.pdf」 는 SFSA2610153. tools/bulk_jobs.case_of_file 과 같은 자르기)."""
    import os
    stem = os.path.splitext(os.path.basename(str(name or "")))[0].strip()
    return stem.split()[0] if stem.split() else ""


def field_available(doc_text):
    """이 서류에 요구 항목별 열이 인쇄돼 있나. 글자가 없으면 판단하지 않는다(None)."""
    import re
    if len((doc_text or "").strip()) < 200:
        return {c: None for c in REQ_HEAD}
    return {c: bool(re.search(p, doc_text)) for c, p in REQ_HEAD.items()}


# 핵심 세 항목은 유상 품목을 SAP 에 넣을 때 사람이 적는 셋이다(PO, Material, 선적 수량. 11/04 PPT 1쪽)
CORE3 = ["Purchase Order", "Material", "B/L quantity"]
CORE3_HEAD = {"Purchase Order": r"(?i)p\s*/?\s*o\.?\s*(no|number)|purchas",
              "Material": r"(?i)\bmaterial\b|sap\s*code|item\s*code",
              "B/L quantity": r"(?i)\bq[\'’]?ty\b|quantity|수량"}
CORE3_COLS = ["케이스", "인보이스 줄", "Purchasing document", "Material", "B/L quantity",
              "셋 다 있는 줄", "서류에 열이 있나"]

# 보충한 값은 출처를 같이 낸다. 인보이스에서 읽은 값과 통관서류에서 가져온 값은 근거가 다르다.
MAIN_COLS = ["케이스", "Invoice No.", "줄"] + [s[1] for s in MAIN_ITEMS] + ["보충 출처", "미추출 사유"]
SUB_COLS = ["케이스", "Invoice No.", "줄"] + [s[1] for s in SUB_ITEMS] + ["INCOTERMS"]
SPEC_COLS = ["등급", "항목", "BL Item 열", "견주는 법", "비면 확인", "설명"]
COMPARE_NAME = {"file": "파일 이름에서 넣음(판독 정확도에서 뺌)", "code": "띄어쓰기, 대소문자 무시", "number": "숫자로", "unit": "표기 통일 뒤", "text": "기호, 띄어쓰기 무시"}

GERP_HEAD_COLS = ["케이스", "Incoterms", "Master B/L No.", "House B/L No.", "Forwarder",
                  "ETD", "ETA", "Shipping Port", "Arrival Port", "Invoice No.",
                  "Invoice Amt", "Invoice Cur", "Gross Weight", "Chargeable Weight",
                  "Package Qty", "Measurement(CBM)", "인보이스 장수", "PO 개수"]
INV_COLS = ["케이스", "Invoice No.", "총액", "통화", "줄 수", "줄 금액 합", "차이"]
REVIEW_COLS = ["케이스", "FAIL", "WARN", "인보이스 줄", "포장명세서 줄", "PO 빈 줄", "품목 코드 빈 줄",
               "FAIL 항목", "WARN 항목", "실패"]

ORIGIN = [
    ("BL Item", "Imp.Req. No.", "GERP", "수입의뢰 번호. 선적 서류에 미인쇄"),
    ("BL Item", "Item", "서류", "인보이스의 Po Item No. 열. 해당 열이 없는 서류는 미기재"),
    ("BL Item", "Purchase Order", "서류", "인보이스의 Po No. 열"),
    ("BL Item", "Material", "서류", "하이닉스 SAP 자재 번호. 통관표 SKhynix SAP Code 열과 같은 코드, 하이닉스 형식(영문으로 시작하는 8자리) 코드 순으로 고름. 공급사 자체 번호는 미기재"),
    ("BL Item", "Vendor Part No.", "서류", "인보이스의 HIOS No. 열. 미인쇄 시 Part No. 열 적용"),
    ("BL Item", "B/L quantity", "서류", "인보이스 행의 수량"),
    ("BL Item", "Invoice amount", "서류", "인보이스 행의 금액"),
    ("BL Item", "Net Price", "서류", "인보이스 행의 단가"),
    ("BL Item", "BL No.", "파일 이름", "올린 PDF 파일 이름(확장자를 빼고 첫 낱말). 우시는 파일 이름이 HBL 번호"),
    ("BL Item", "Currency", "서류", "그 행이 속한 인보이스의 통화. 인보이스 총액 옆 통화를 행마다 잇음"),
    ("BL Header", "Incoterms", "서류", "인보이스의 인코텀즈"),
    ("BL Header", "Master B/L No.", "서류", "운송장의 마스터 번호"),
    ("BL Header", "House B/L No.", "서류", "운송장의 하우스 번호"),
    ("BL Header", "Forwarder", "서류", "미추출. 하우스 운송장 발행자 칸 소재"),
    ("BL Header", "ETD", "스케줄", "출항일. 선사 및 항공사 스케줄 기준"),
    ("BL Header", "ETA", "스케줄", "입항일. 선사 및 항공사 스케줄 기준"),
    ("BL Header", "Shipping Port", "서류", "운송장의 출발 공항 또는 선적항"),
    ("BL Header", "Arrival Port", "서류", "운송장의 도착 공항 또는 양륙항"),
    ("BL Header", "Invoice Amt", "서류", "인보이스 총액. 복수 시 합계. 장별 금액은 「인보이스」 시트 참조"),
    ("BL Header", "Gross Weight", "서류", "운송장의 총중량"),
    ("BL Header", "Chargeable Weight", "서류", "운송장의 청구 중량. 해상은 미기재"),
    ("BL Header", "Measurement(CBM)", "서류", "운송장 또는 포장명세서의 용적"),
]
NOTES = [
    "노란색 행: PO 번호 또는 품목 코드 미추출. 해당 행만 확인합니다.",
    "회색 열: GERP 에서 입력하는 열. 선적 서류에 인쇄되지 않습니다.",
    "출처 PL: 포장명세서에만 존재하는 행. 금액과 단가 없음.",
    "서류에 인쇄된 코드 열 전체를 병기합니다. Material 이 빈 줄은 이 열들로 원문을 확인합니다.",
    "Main 항목과 Sub 항목의 정의는 「항목 정의」 시트를 봅니다.",
]


def load_preds(path):
    """파일 하나 또는 폴더. docpipe --save 와 batch_run 과 확인 화면이 남긴 모양을 다 받는다."""
    path = os.path.abspath(os.path.expanduser(path))
    files = ([path] if os.path.isfile(path) else
             [os.path.join(path, f) for f in sorted(os.listdir(path))
              if f.endswith(".json") and not f.startswith("_")])
    out = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            rec = json.load(fh)
        base = os.path.basename(f)[:-5]
        if isinstance(rec, list):
            out.extend(rec)
        elif isinstance(rec, dict) and rec and all(isinstance(v, dict) for v in rec.values()) \
                and not ({"ex", "raw", "ci_lines", "hawb", "case"} & set(rec)):
            # docpipe 는 꾸러미 이름을 열쇠로 하는 사전을 낸다. --save 는 날것만,
            # --save-full 은 후처리한 값과 검증 결과를 같이 담는다.
            for name, v in rec.items():
                if "ex" in v or "verify" in v:
                    out.append({"case": name, "ex": v.get("ex"), "verify": v.get("verify")})
                else:
                    out.append({"case": name, "raw": v})
        else:
            rec.setdefault("case", base)
            out.append(rec)
    return out


DOC_ROOT = None      # --docs 로 준 원본 폴더. 주면 서류 글을 읽어 보정을 한 번 더 건다.


_TEXT_CACHE = {}


def _doc_text(name):
    """이 케이스의 서류 글. 같은 케이스를 두 번 읽지 않는다."""
    if name in _TEXT_CACHE:
        return _TEXT_CACHE[name]
    txt = ""
    if DOC_ROOT:
        import glob
        # 글자층이 없으면 OCR 까지 간다. 전면 스캔 서류에서 PO 를 살리는 자리다.
        txt = docpipe.doc_text_or_ocr(sorted(glob.glob(os.path.join(DOC_ROOT, name, "*.pdf"))))
    _TEXT_CACHE[name] = txt
    return txt


def _fill_from_docs(name, ex):
    """서류에 한 번만 찍힌 원산지를 줄에 채우고 근거 없는 코드를 지운다(docpipe.hynix_fill).

    🔴 왜 여기서 또 부르나. 배치가 남기는 것은 모델이 낸 날것이라 보정이 안 들어가 있다.
       화면은 build_case 에서 걸지만 엑셀은 그 길을 안 지난다. 두 산출물의 값이 다르면
       받는 쪽이 어느 것을 믿어야 할지 모른다.
    """
    if not DOC_ROOT:
        return ex
    import glob
    try:
        import pymupdf
    except Exception:
        return ex
    import glob as _g
    _p = sorted(_g.glob(os.path.join(DOC_ROOT, name, '*.pdf'))) if DOC_ROOT else []
    return docpipe.hynix_fill(ex, _doc_text(name), docpipe.doc_has_scan(_p))


def extraction(rec):
    """저장된 모양이 넷이라 여기서 하나로 모은다. 없으면 None 이다."""
    if rec.get("ex"):
        return rec["ex"]
    if rec.get("raw"):
        return docpipe.postprocess(rec["raw"])
    if isinstance(rec.get("case"), dict):
        return (rec["case"] or {}).get("ex")
    if "ci_lines" in rec or "hawb" in rec:
        return rec
    return None


def case_name(rec):
    c = rec.get("case")
    if isinstance(c, dict):
        return c.get("case") or "케이스"
    return c or "케이스"


def _s(v):
    return "" if v is None else v


def pick(line, keys):
    """GERP_MAP 의 순서대로 먼저 찬 값. 하나도 없으면 빈칸이다."""
    for k in keys:
        v = line.get(k)
        if v not in (None, ""):
            return v
    return ""


_HYNIX_MAT = re.compile(r"^[A-Z][A-Z0-9]{7}$")       # R1257986, P3024498, SZ967770, S31A0156, EBE05551
_HYNIX_STRICT = re.compile(r"^[A-Z]\d{7}$")           # 영문 하나 뒤 숫자 일곱(P3024498). 부품 번호 칸에서도 믿는 모양
_PLAIN_CODE = re.compile(r"^[A-Z][A-Z0-9]{5,11}$")    # 붙임표 없는 영문 시작 코드. 형식 밖이라도 차선으로 둔다


def _code(v):
    return re.sub(r"\s+", "", str(v if v is not None else "")).upper()


def is_hynix_code(v):
    v = _code(v)
    return bool(_HYNIX_MAT.match(v)) and sum(ch.isdigit() for ch in v) >= 3


def cn_code_set(ex):
    """통관표의 SKhynix SAP Code 열에 찍힌 코드. 하이닉스가 자기 자재 번호로 적은 자리다."""
    return {_code(x.get("sap_code")) for x in (ex.get("cn_lines") or [])
            if isinstance(x, dict) and _PLAIN_CODE.match(_code(x.get("sap_code")))}


def part_col_is_hynix(lines):
    """묶음의 부품 번호 칸이 사실상 하이닉스 자재 번호 열인가. 절반 넘게 영문 하나와 숫자 일곱이면 그렇다고 본다.

    중국 통관 인보이스는 「料件号」(공급사 번호) 와 「客户料号」(고객 자재 번호) 를 같이 찍고, 모델은 뒤쪽을
    부품 번호 칸으로 읽는다(WUX-W260200, 11/10). 그 열에는 S2M21062 같은 다른 모양의 하이닉스 번호도 섞인다.
    """
    vals = [_code(x.get("part_no")) for x in lines if isinstance(x, dict) and _code(x.get("part_no"))]
    return bool(vals) and sum(1 for v in vals if _HYNIX_STRICT.match(v)) * 2 > len(vals)


def hynix_material(line, cn_codes=(), part_col=False):
    """줄에서 하이닉스 SAP 자재 번호를 고른다. (값, 고른 칸) 이고, 없으면 ("", None) 이다.

    🔴 왜 열 이름으로 안 고르나. 같은 하이닉스 자재 번호를 공급사마다 「Sap Code」 열에도 「Material」 열에도
       찍는다(46건, 11/10). 한 공급사는 「Sap Code」 열에 자기 9자리 코드(AA0400575)를 찍고 하이닉스 번호(P3024498)는
       Material 열에 찍는다. 예전처럼 Sap Code 열을 먼저 쓰면 그 공급사의 Material 칸에 엉뚱한 코드가 갔다.
    🔴 고르는 차례
       0. 운영 화면에서 반영한 코드 사전이 채운 자재 코드(wuxi_rules.apply_post 가 줄의 _rules 에 적는다)
       1. 통관표 SKhynix SAP Code 열에 같은 값이 있는 코드(두 서류가 맞춰 준 값)
       2. Sap Code 열이나 Material 열의 하이닉스 형식 코드(영문으로 시작하는 8자리, 숫자 셋 이상)
       3. 부품 번호 칸의 영문 하나와 숫자 일곱(사본마다 칸이 바뀌어 읽히는 일이 있다). 묶음의 부품 번호 칸이
          하이닉스 번호 열이면(part_col_is_hynix) 다른 하이닉스 형식도 받는다
       4. Sap Code 열이나 Material 열의 붙임표 없는 영문 시작 코드(형식을 다 본 것이 아니라 차선으로 남긴다)
       공급사 자체 자재 번호(23-10101-55-JGN 같은 모양)는 넣지 않는다. 고객 PPT 가 「자재 모델은 표시되나
       Material Code 미표시」 로 가르는 경우다. 비워 두면 사람이 보고 채운다. 틀린 코드보다 낫다.
    """
    fields = ("sap_code", "material_code", "part_no", "hios_no")
    by_rule = line.get("_rules") if isinstance(line.get("_rules"), dict) else {}
    for k in ("sap_code", "material_code"):        # 0. 운영자가 코드 사전으로 정한 값은 모양을 따지지 않는다(T-092-0112MC, 2310685)
        if k in by_rule and _code(line.get(k)):
            return str(line.get(k)).strip(), k
    vals = [(k, str(line.get(k)).strip()) for k in fields if _code(line.get(k))]
    for k, v in vals:
        if _code(v) in cn_codes:
            return v, k
    for k, v in vals:
        if k in ("sap_code", "material_code") and is_hynix_code(v):
            return v, k
    for k, v in vals:
        if k == "part_no" and (_HYNIX_STRICT.match(_code(v)) or (part_col and is_hynix_code(v))):
            return v, k
    for k, v in vals:
        if k in ("sap_code", "material_code") and _PLAIN_CODE.match(_code(v)):
            return v, k
    return "", None


_NCV_WORDS = re.compile(r"(?i)\bN\.?\s?C\.?\s?V\b\.?|NO\s+COMMERCIAL\s+VALUE|FREE\s+OF\s+CHARGE|\bF\.?O\.?C\b\.?|"
                        r"\bSAMPLES?\b|无商业价值|免费|무상")


def ncv_hint(line):
    """무상 품목으로 볼 근거. 판정은 하지 않는다. 사람이 보고, 쌓이면 2차에서 구분 규칙을 만든다."""
    out = []
    for k in ("description", "amount", "unit_price", "remark", "note"):
        v = line.get(k)
        if isinstance(v, str):
            m = _NCV_WORDS.search(v)
            if m and m.group(0).strip() not in out:
                out.append(m.group(0).strip())
    price = docpipe.num(line.get("unit_price"))
    qty = docpipe.num(line.get("qty"))
    if price == 0 and qty:
        out.append("단가 0")
    return ", ".join(out)


def currency_of(ex):
    """인보이스 번호 → 통화. 번호를 모르는 줄에 쓸 값(인보이스가 한 장이거나 머리 통화)도 함께 낸다."""
    per = {}
    for t in ex.get("invoice_totals") or []:
        if isinstance(t, dict) and str(t.get("currency") or "").strip():
            per[str(t.get("invoice_no") or "")] = str(t["currency"]).strip()
    codes = set(per.values())
    default = str(ex.get("currency") or "").strip() or (next(iter(codes)) if len(codes) == 1 else "")
    return per, default


def add_currency(items, inv_rows):
    """이미 만든 품목 줄에 통화를 채운다. 통화 열이 생기기 전에 읽은 작업과 화면에서 받은 줄에 쓴다.

    inv_rows 는 「인보이스」 표의 행(Invoice No., 통화)이다. 이미 값이 있는 줄은 건드리지 않는다.
    """
    per = {str(r.get("Invoice No.") or ""): str(r.get("통화") or "").strip()
           for r in inv_rows or [] if isinstance(r, dict) and str(r.get("통화") or "").strip()}
    codes = set(per.values())
    only = next(iter(codes)) if len(codes) == 1 else ""
    n = 0
    for r in items or []:
        if not isinstance(r, dict) or str(r.get("Currency") or "").strip():
            continue
        v = per.get(str(r.get("Invoice No.") or "")) or only
        r["Currency"] = v
        n += bool(v)
    return n


def _codes(pairs):
    """{칸: 코드}. 빈 칸은 뺀다."""
    return {k: _code(v) for k, v in pairs if _code(v)}


def at_odds(po_a, codes_a, po_b, codes_b):
    """두 줄이 서로 다른 품목이라는 근거가 있나.

    PO 가 둘 다 있는데 다르면 그렇다. 코드는 같은 칸끼리 견줄 때만 근거로 삼는다. 인보이스에는 SAP CODE 가,
    포장명세서에는 Part No. 가 찍히는 서류가 있어(AATSN2602139) 종류가 다른 코드가 다른 것은 근거가 못 된다.
    다만 사본마다 칸이 바뀌어 읽히는 일이 있으므로, 어느 칸에서든 같은 값이 하나라도 있으면 같은 품목으로 본다.
    """
    if _code(po_a) and _code(po_b) and _code(po_a) != _code(po_b):
        return True
    if set(codes_a.values()) & set(codes_b.values()):
        return False
    # 한쪽 값이 다른 쪽에 들어 있으면 같은 값이다. 포장명세서가 시리얼 칸에 모델까지 붙여 찍는다
    # (HDLCA112889: 인보이스 「390」, 포장명세서 「[4S267-078-2 ] 390」)
    return any(not (codes_a[k] in codes_b[k] or codes_b[k] in codes_a[k]) for k in codes_a if k in codes_b)


def qty_pairs(ci, pl, taken_ci=(), taken_pl=(), single_inv=False, odd=None):
    """코드로 못 이은 인보이스 줄과 포장명세서 줄을 같은 인보이스 안의 같은 수량으로 잇는다. {인보이스 줄: 포장 줄}.

    ci, pl 은 (인보이스 번호, 수량) 목록이다. 번호가 빈 줄은 묶음의 인보이스가 한 장일 때만 잇는다.
    🔴 왜 수량인가. 인보이스에만 코드가 있고 포장명세서 줄에는 코드도 PO 도 없는 서류가 흔하다(AATSN2601238 은
       코드 1014869 와 수량 264 인 줄이 서로 못 이어져 품목 줄이 두 줄로 나왔다. 46건 중 17건, 11/10). 수량이 같고
       같은 인보이스면 같은 품목으로 본다. 같은 수량이 여럿이면 인쇄 차례대로 하나씩 쓴다. 틀리게 이어도 포장 칸
       (케이스 번호, 중량)만 옮겨 가고 품목 줄은 빠지지 않는다.
       odd(i, j) 가 참이면(PO 나 코드가 서로 다르면, at_odds) 수량이 같아도 잇지 않는다. 팔레트처럼 포장명세서에만
       있는 줄이 우연히 같은 수량일 수 있다.
    """
    out, used = {}, set(taken_pl)
    for i, (inv, q) in enumerate(ci):
        if i in taken_ci or q is None:
            continue
        for j, (pinv, pq) in enumerate(pl):
            if j in used or pq is None or pq != q or (odd and odd(i, j)):
                continue
            if inv and pinv and inv != pinv:
                continue
            if not (inv and pinv) and not single_inv:
                continue
            out[i] = j
            used.add(j)
            break
    return out


PL_COLS = ("Case No.", "순중량(kg)", "총중량(kg)")      # 포장명세서에서 인보이스 줄로 옮겨 오는 칸


def join_pl_rows(items):
    """이미 만든 품목 줄 가운데 못 이은 포장명세서 줄(출처 PL)을 같은 수량의 인보이스 줄에 합친다. 합친 줄 수를 낸다.

    판독 코드가 수량으로 잇기 전에 읽은 작업에 쓴다(tools/jobs_backfill.py). 결과는 item_rows 와 같다.
    """
    ci_i = [k for k, r in enumerate(items) if r.get("출처") == "CI"
            and not any(str(r.get(c) or "").strip() for c in PL_COLS)]
    pl_i = [k for k, r in enumerate(items) if r.get("출처") == "PL"]
    invs = {str(items[k].get("Invoice No.") or "") for k, r in enumerate(items) if r.get("출처") == "CI"} - {""}

    def sig(r):
        return r.get("Purchase Order"), _codes((c, r.get(c)) for c, _ in CODE_COLS if c != "Po Item No.")

    pairs = qty_pairs([(str(items[k].get("Invoice No.") or ""), docpipe.num(items[k].get("B/L quantity"))) for k in ci_i],
                      [(str(items[k].get("Invoice No.") or ""), docpipe.num(items[k].get("B/L quantity"))) for k in pl_i],
                      single_inv=len(invs) <= 1,
                      odd=lambda a, b: at_odds(*sig(items[ci_i[a]]), *sig(items[pl_i[b]])))
    drop = set()
    for a, b in pairs.items():
        row, p = items[ci_i[a]], items[pl_i[b]]
        for c in PL_COLS:
            row[c] = p.get(c, "")
        drop.add(pl_i[b])
    items[:] = [r for k, r in enumerate(items) if k not in drop]
    return len(drop) + sum(fold_pl_rows(items).values())


# 품목을 가리키는 코드 칸(줄 형태). PO 품목 번호와 I/V 번호는 품목이 아니라 자리를 가리키므로 뺀다
IDENT_COLS = ("Material", "Vendor Part No.", "Sap Code", "Material Code", "Part No.", "HIOS No.", "Serial No.")


def fold_pl_rows(rows):
    """품목 줄 가운데 남은 포장명세서 줄(출처 PL)을 정리한다. 고친 rows 와 {"joined", "same_item", "boxes"} 줄 수.

    G-ERP 품목 줄은 인보이스 줄이다(납품 수량은 인보이스에 인쇄된 수량, 11/10). 그런데 못 이은 포장명세서 줄이 따로
    품목 줄로 나와, 정답 46건에서 사람이 PL 줄 55줄을 지웠다(11/12 정확도 기준선). 셋으로 정리한다.

      joined     코드가 어느 칸에서든 겹치는 인보이스 줄이 하나뿐이면 잇는다. 인보이스는 SAP 코드를, 포장명세서는
                 다른 칸 코드를 먼저 봐서 못 이은 줄(HDLCA112870)
      same_item  인보이스에 같은 품목(코드, 코드가 없으면 품명)이 있고, 그 품목의 남은 PL 수량 합이 인보이스 수량을
                 넘지 않으면 품목 줄로 내지 않는다. 수량이 비었거나 단위가 다르면(무게 KG) 수량은 견주지 않는다.
                 상자별로 나뉜 줄(PLISZ2H00667), 사본(HDLCA112870), 무게 줄(PNS26090853)
      boxes      품명도 코드도 없는 PL 줄은, 수량 합이 인보이스 줄 가운데 아직 PL 과 안 이은 수량 합과 꼭 같을 때만
                 상자 나눔으로 본다(PLISZ2H00693)

    🔴 수량이 넘치거나 맞지 않으면 줄을 그대로 둔다. 인보이스 수량을 잘못 읽었다는 신호일 수 있다
       (PLISZ2H00644: 인보이스 215 를 1 로 읽음, PL 은 150+15+22+28). 빠짐 검사는 서류의 줄 수로 세므로 이 정리와 상관없다.
    """
    out = {"joined": 0, "same_item": 0, "boxes": 0}
    ci = [r for r in rows if r.get("출처") == "CI"]
    if not ci:
        return out
    invs = {_s(r.get("Invoice No.")) for r in ci} - {""}

    def codes(r):
        # 글자와 숫자만 견준다. 「LX-SMA078X232-03 260203」 과 「LX-SMA078X232-03_260203」 은 같은 코드다(PLISZ2H00486)
        return {_alnum(r.get(c)) for c in IDENT_COLS if _alnum(r.get(c))}

    def same_inv(a, b, loose=False):
        ia, ib = _s(a.get("Invoice No.")), _s(b.get("Invoice No."))
        # 포장명세서 줄은 인보이스 번호를 안 찍는 일이 많다. 같은 품목 찾기(loose)에서는 번호 없는 줄을 어느 장과도 견준다
        return ia == ib if ia and ib else (loose or len(invs) <= 1)

    def item_of(p, r):
        """PL 줄 p 와 인보이스 줄 r 이 같은 품목인가. 2: 코드로 맞음, 1: 품명으로만 맞음, 0: 다름.
        코드가 겹치거나, 한쪽 품명에 다른 쪽 코드가 들어 있으면(PLISZ2H00667) 코드로 맞는다. PL 줄에 코드가 없으면 품명으로 본다.
        같은 품명의 인보이스 줄이 여럿이면 코드로 맞는 줄만 쓴다(아래 same_item).

        🔴 예전에는 인보이스 줄에도 코드가 없어야 품명으로 견줬다. 그런데 포장명세서는 코드를 안 찍고 인보이스만 찍는
           서류가 흔해, 품명이 꼭 같은 PL 줄이 따로 품목 줄로 남았다(우시 9월 HDLCA124559 VERSUM 3줄, FLU10638 DUPONT
           CMP 5줄, 11/17). PL 줄에 코드가 있는데 안 겹치면 여전히 다른 품목으로 본다."""
        pc, rc = codes(p), codes(r)
        if pc & rc:
            return 2
        # 포장명세서가 코드 뒤에 내부 번호를 붙여 찍는다(PLISZ2H00821: 인보이스 EAX01927933, PL EAX01927933/1015585)
        if any(len(a) >= 6 and len(b) >= 6 and (a in b or b in a) for a in pc for b in rc):
            return 2
        pd, rd = _alnum(p.get("품명")), _alnum(r.get("품명"))
        if any(len(c) >= 6 and c in pd for c in rc):
            return 2
        # 거꾸로 인보이스 품명에 PL 코드가 들어 있다(SFSA2610028: 인보이스 「00012097013 X IKONIC…」, PL 코드 12097013)
        if any(len(c) >= 6 and c in rd for c in pc):
            return 2
        if pc:
            return 0
        a, b = _norm_desc(_LOT_TAIL.sub("", _s(p.get("품명")))), _norm_desc(r.get("품명"))
        if not a:
            return 0
        # PL 품명이 인보이스 품명의 일부이고 뒤에 LOT 번호만 붙는다(NEC49849015 TOPCO: 인보이스 「PHOTO RESIST NP _SOC…」,
        # PL 「NP _SOC… Lot No.260612D1」). 짧은 낱말이 우연히 들어 있는 것을 막으려고 12자 넘을 때만 본다
        return 1 if a == b or (min(len(a), len(b)) >= 12 and (a in b or b in a)) else 0

    def joined(r):
        return any(_s(r.get(c)) for c in PL_COLS) or r.get("_pl_qty") is not None

    def q(r):
        return docpipe.num(r.get("B/L quantity"))

    def unit(r):
        return _norm_unit(r.get("단위"))

    drop = set()
    pls = [k for k, r in enumerate(rows) if r.get("출처") == "PL"]
    # joined: 코드가 겹치는 안 이은 인보이스 줄이 꼭 하나
    for k in pls:
        p, cs = rows[k], codes(rows[k])
        if not cs:
            continue
        hit = [r for r in ci if not joined(r) and same_inv(r, p) and codes(r) & cs]
        if len(hit) == 1:
            for c in PL_COLS:
                hit[0][c] = p.get(c, "")
            hit[0]["_pl_qty"] = q(p)
            drop.add(k)
            out["joined"] += 1
    # same_item: 같은 품목의 인보이스 줄 묶음마다
    groups = {}
    for k in pls:
        if k in drop:
            continue
        hit = {i: item_of(rows[k], r) for i, r in enumerate(ci) if same_inv(r, rows[k], loose=True)}
        best = max(hit.values(), default=0)
        m = [i for i, v in hit.items() if v and v == best]
        if m:
            groups.setdefault(tuple(m), []).append(k)
    for m, ks in groups.items():
        cq = [q(ci[i]) for i in m]
        cu = {unit(ci[i]) for i in m} - {""}
        counted = [k for k in ks if q(rows[k]) is not None and not (unit(rows[k]) and cu and unit(rows[k]) not in cu)]
        if counted and (None in cq or sum(q(rows[k]) for k in counted) > sum(cq) + 1e-9):
            continue
        first = ci[m[0]]
        for k in ks:
            cn = _s(rows[k].get("Case No."))
            have = [x.strip() for x in _s(first.get("Case No.")).split(",") if x.strip()]
            if cn and cn not in have:
                first["Case No."] = ", ".join(have + [cn])
            drop.add(k)
            out["same_item"] += 1
    # boxes: 품명도 코드도 없는 PL 줄의 수량 합이 안 이은 인보이스 수량과 같을 때
    bare = [k for k in pls if k not in drop and not codes(rows[k]) and not _norm_desc(rows[k].get("품명"))]
    if bare and all(q(rows[k]) is not None for k in bare):
        open_q = [q(r) for r in ci if not joined(r)]
        if open_q and None not in open_q and abs(sum(open_q) - sum(q(rows[k]) for k in bare)) < 1e-9:
            drop.update(bare)
            out["boxes"] += len(bare)
    # 수량이 상자 수(CTN, PLT …)인 PL 줄은 G-ERP 수량이 될 수 없다. 인보이스 줄에 코드가 하나도 없어 품목을 맞춰 볼 수
    # 없을 때만 상자 줄로 본다(PLISZ2H01140: 인보이스는 품명뿐, PL 은 부품 번호와 상자 수). 인보이스에 코드가 있는데
    # 안 맞는 PL 줄은 인보이스가 빠뜨린 품목일 수 있으니 그대로 둔다
    if not any(codes(r) for r in ci):
        packs = [k for k in pls if k not in drop and _norm_unit(rows[k].get("단위")) in PACK_UNITS]
        drop.update(packs)
        out["boxes"] += len(packs)
    rows[:] = [r for k, r in enumerate(rows) if k not in drop]
    return out


PACK_UNITS = {"CTN", "CTNS", "CARTON", "CARTONS", "BOX", "BOXES", "PLT", "PLTS", "PALLET", "PALLETS", "PKG", "PKGS",
              "PACKAGE", "PACKAGES", "CASE", "CASES", "CS", "SKID", "SKIDS", "CRATE", "CRATES"}


_LOT_TAIL = re.compile(r"\s*\bLOT\s*(?:NO\.?|#)?\s*[:.]?\s*\S+.*$", re.I)     # 품명 뒤에 붙은 「Lot No.xxx」


def _alnum(v):
    return re.sub(r"[^0-9A-Z]", "", _s(v).upper())


def _norm_desc(v):      # wuxi_rules.norm_desc 와 같은 자. gerp_xlsx 는 규칙 저장소에 기대지 않는다
    return re.sub(r"[^0-9A-Z가-힣一-鿿]+", " ", re.sub(r"\s+", " ", _s(v)).upper()).strip()


def _norm_unit(v):
    return re.sub(r"[^A-Z]", "", _s(v).upper())


def line_code(line):
    """인보이스 줄과 포장명세서 줄을 잇는 열쇠. 인쇄된 코드 중 먼저 찬 것이다."""
    for _, k in CODE_COLS:
        v = str(line.get(k) or "").strip()
        if v:
            return v
    return ""


def head_row(name, ex):
    return {
        "케이스": name,
        "Incoterms": _s(ex.get("incoterms")),
        "Master B/L No.": _s(ex.get("mawb")),
        "House B/L No.": _s(ex.get("hawb")),
        "Forwarder": "",
        "ETD": "", "ETA": "",
        "Shipping Port": _s(ex.get("origin")),
        "Arrival Port": _s(ex.get("destination")),
        "Invoice No.": _s(ex.get("invoice_no")),
        "Invoice Amt": _s(ex.get("ci_total")),
        "Invoice Cur": _s(ex.get("currency")),
        "Gross Weight": _s(ex.get("awb_gross")),
        "Chargeable Weight": _s(ex.get("awb_chargeable")),
        "Package Qty": _s(ex.get("awb_pieces")),
        "Measurement(CBM)": _s(ex.get("measurement_cbm")),
        "인보이스 장수": len(ex.get("invoice_totals") or []),
        "PO 개수": len([x for x in (ex.get("po_list") or []) if str(x).strip()]),
    }


def item_rows(name, ex):
    """인보이스 줄이 본체다. 포장명세서 줄은 인보이스 번호와 코드로 이어 붙인다.

    🔴 왜 인보이스가 본체인가. GERP 품목 표가 금액과 단가를 받는데 그 둘이 인보이스에만
       있다. 포장명세서는 중량과 케이스 번호를 준다. 그래서 인보이스 줄에 포장명세서
       줄을 붙이는 방향이다. 반대로 하면 금액 없는 줄이 생긴다.

    🔴 왜 인보이스 번호까지 열쇠에 넣나. 한 묶음에 인보이스가 네 장 들어오고 같은 품목
       코드가 장마다 되풀이되는 서류가 있다. 코드만으로 이으면 1월 인보이스 줄에 4월
       포장명세서의 중량이 붙는다.
    """
    ci = [x for x in (ex.get("ci_lines") or []) if isinstance(x, dict)]
    pl = [x for x in (ex.get("pl_lines") or []) if isinstance(x, dict)]

    # 🔴 열쇠를 좁은 것부터 넓은 것으로 셋 둔다. 한 묶음의 줄이 열일곱인데 품목 코드가
    #    전부 같은 서류가 있다(같은 부품을 여러 번 수리해 보낸 건). 코드만으로 이으면
    #    열일곱 줄이 한 줄에 몰리고 나머지 열여섯이 짝 없는 줄로 떨어진다. 그래서 인보이스
    #    번호와 PO 와 PO 품목 번호까지 넣어 좁히고, 같은 열쇠가 여러 줄이면 인쇄 순서대로
    #    하나씩 쓴다. 한 줄은 한 번만 쓰인다.
    # 🔴 포장명세서는 인보이스 번호를 머리에만 찍고 줄에는 안 찍는 일이 많다. 묶음의 인보이스가 한 장뿐이면
    #    번호 없는 줄은 그 장의 줄로 본다(10/29 이관 점검에서 찾음). 여러 장이면 어느 장인지 모르니 그대로 둔다
    invs = {str(x.get("invoice_no")) for x in ci if x.get("invoice_no")}
    only_inv = next(iter(invs)) if len(invs) == 1 else ""

    def keys_of(x):
        inv, code = str(x.get("invoice_no") or only_inv), line_code(x)
        po, item = str(x.get("po_no") or ""), str(x.get("po_item_no") or "")
        # 🔴 인보이스 번호, PO, PO 품목 번호가 다 있으면 코드 없이도 잇는다(10/29 이관 점검에서 찾음).
        #    인보이스에는 SAP 열이 있고(또는 통관표에서 채워지고) 포장명세서에는 코드 열이 없는 묶음에서,
        #    코드가 든 열쇠만으로는 한 줄도 안 이어져 엑셀 품목 줄이 두 배가 됐다
        strong = [("po-item", inv, po, item)] if inv and po and item else []
        return [(inv, po, item, code), *strong, (inv, code), (code,)]

    buckets = {}
    for i, x in enumerate(pl):
        for k in keys_of(x):
            if any(k):
                buckets.setdefault(k, []).append(i)
    used = set()

    def take(x):
        for k in keys_of(x):
            for i in buckets.get(k, []):
                if i not in used:
                    used.add(i)
                    return pl[i]
        return {}

    cn_codes = cn_code_set(ex)
    part_col = {"ci": part_col_is_hynix(ci), "pl": part_col_is_hynix(pl)}
    cur_per, cur_default = currency_of(ex)

    def currency(x):
        return cur_per.get(str(x.get("invoice_no") or only_inv)) or cur_default

    def material(row, x, side="ci"):
        v, k = hynix_material(x, cn_codes, part_col[side])
        row["Material"] = v
        row["_mat_key"] = k          # 사람이 이 칸을 고치면 어느 판독 칸의 학습인지(wuxi_learn.field_for)
        if not v:
            raw = pick(x, MAP_KEY["Material"])
            if raw:
                row["_mat_raw"] = str(raw)

    paired = [take(x) for x in ci]
    # 코드로 못 이은 줄은 같은 인보이스 안의 같은 수량으로 한 번 더 잇는다(qty_pairs)
    left_pl = [j for j in range(len(pl)) if j not in used]
    def sig(x):
        return x.get("po_no"), _codes((k, x.get(k)) for _, k in CODE_COLS if k != "po_item_no")

    extra = qty_pairs([(str(x.get("invoice_no") or only_inv), docpipe.num(x.get("qty"))) for x in ci],
                      [(str(pl[j].get("invoice_no") or only_inv), docpipe.num(pl[j].get("qty"))) for j in left_pl],
                      taken_ci={i for i, p in enumerate(paired) if p}, single_inv=len(invs) <= 1,
                      odd=lambda i, k: at_odds(*sig(ci[i]), *sig(pl[left_pl[k]])))
    for i, k in extra.items():
        paired[i] = pl[left_pl[k]]
        used.add(left_pl[k])

    rows = []
    for i, x in enumerate(ci, 1):
        p = paired[i - 1]
        row = {c: pick(x, MAP_KEY[c]) for c in GERP_ITEM_COLS}
        material(row, x)
        row["NCV 단서"] = ncv_hint(x)
        row["Currency"] = currency(x)
        row.update({
            "케이스": name, "Invoice No.": _s(x.get("invoice_no")), "출처": "CI",
            "줄": x.get("seq") if x.get("seq") is not None else i,
            "품명": _s(x.get("description")), "단위": _s(x.get("unit")),
            "Case No.": _s(p.get("case_no")),
            "순중량(kg)": _s(p.get("net")), "총중량(kg)": _s(p.get("gross")),
            "HS": _s(x.get("hs_code")), "원산지": _s(x.get("country_of_origin")),
        })
        row.update({c: _s(x.get(k)) for c, k in CODE_COLS})
        # 어디서 온 값인지 화면이 표시할 수 있게 같이 싣는다
        srcs = []
        if x.get("_po_from"): srcs.append("P/O " + x["_po_from"])
        if x.get("_sap_from"): srcs.append("SAP " + x["_sap_from"])
        if x.get("_mat_from"): srcs.append("자재 " + x["_mat_from"])
        if x.get("_org_from"): srcs.append("원산지 " + x["_org_from"])
        row["_src"] = ", ".join(srcs)
        row["_unver"] = bool(x.get("_unverified"))
        if p:
            row["_pl_qty"] = docpipe.num(p.get("qty"))    # 이은 PL 줄의 수량(fold_pl_rows 가 이은 줄인지 봄)
        rows.append(row)
    for j, x in enumerate(pl):
        if j in used:
            continue
        row = {c: pick(x, MAP_KEY[c]) for c in GERP_ITEM_COLS}
        material(row, x, "pl")
        row["NCV 단서"] = ncv_hint(x)
        row["Currency"] = currency(x)
        row["Invoice amount"] = ""; row["Net Price"] = ""
        row.update({
            "케이스": name, "Invoice No.": _s(x.get("invoice_no")), "출처": "PL",
            "줄": x.get("seq") if x.get("seq") is not None else j + 1,
            "품명": _s(x.get("description")), "단위": _s(x.get("unit")),
            "Case No.": _s(x.get("case_no")),
            "순중량(kg)": _s(x.get("net")), "총중량(kg)": _s(x.get("gross")),
            "HS": "", "원산지": "",
        })
        row.update({c: _s(x.get(k)) for c, k in CODE_COLS})
        rows.append(row)
    fold_pl_rows(rows)
    return rows


def req_rows(name, ex, avail=None):
    """Main 항목과 Sub 항목을 인보이스 줄마다 한 행으로 낸다. INCOTERMS 는 선적 단위라 줄마다 같다.

    값은 BL Item 행에서 그대로 가져온다. 시트마다 따로 고르면 같은 줄의 Material Code 가 시트마다 달라진다.
    """
    ci = [x for x in (ex.get("ci_lines") or []) if isinstance(x, dict)]
    rows = [r for r in item_rows(name, ex) if r.get("출처") == "CI"]
    out = []
    for x, row in zip(ci, rows):
        r = {"케이스": name, "Invoice No.": row.get("Invoice No.", ""), "줄": row.get("줄"),
             "INCOTERMS": _s(ex.get("incoterms"))}
        for _, label, col, *_ in ITEM_SPEC:
            r[label] = _s(row.get(col))
        src = []
        if x.get("_po_from"): src.append(f"PO No.: {x['_po_from']}")
        if x.get("_sap_from"): src.append(f"Sap Code: {x['_sap_from']}")
        if x.get("_mat_from"): src.append(f"Material: {x['_mat_from']}")
        if x.get("_org_from"): src.append(f"원산지: {x['_org_from']}")
        r["보충 출처"] = ", ".join(src)
        # 🔴 빈칸을 그냥 두지 않는다. 왜 비었는지 적어야 받는 쪽이 판단할 수 있다.
        #    「우리가 못 읽은 것」 과 「서류에 없는 것」 은 다른 말이다.
        why = []
        if x.get("_dropped"):
            why.append("원문 대조 실패로 제거: " + ", ".join(x["_dropped"][:2]))
        if row.get("_mat_raw"):
            why.append(f"Material Code: 서류 코드가 하이닉스 자재 번호 형식이 아님({row['_mat_raw']})")
        av = avail or {}
        # 부품 번호는 열이 없는 서류가 흔해 여기 넣지 않는다(넣으면 거의 모든 줄에 붙는다)
        blanks = [c for c in ("PO No.", "Material Code") if not str(r.get(c, "")).strip()]
        if av.get("시리얼 번호") and not str(r.get("시리얼 번호", "")).strip():
            blanks.append("시리얼 번호")      # 그 열이 있는 서류인데 비었다
        if row.get("_mat_raw") and "Material Code" in blanks:
            blanks.remove("Material Code")
        no_col = [c for c in blanks if av.get(c) is False]
        unknown = [c for c in blanks if c not in no_col]
        if no_col:
            why.append("서류에 해당 열 없음: " + ", ".join(no_col))
        if unknown and not why:
            why.append("확인 필요: " + ", ".join(unknown))
        if x.get("_unverified"):
            why.append("이미지 판독(원문 대조 불가)")
        r["미추출 사유"] = " / ".join(why)
        r["_avail"] = avail or {}
        out.append(r)
    return out


def core3_rows(name, ex, doc_text=""):
    """핵심 세 항목이 줄마다 찼는지, 그리고 서류에 그 열이 아예 없는지.

    🔴 왜 열 유무를 같이 적나. 「Material 45%」 만 내면 우리가 절반을 놓친 것으로 읽힌다.
       실제로는 그 서류에 Material 열이 인쇄돼 있지 않다. 못 읽은 것과 없는 것은 다른
       말이고, 받는 쪽이 판단할 근거가 그 구분이다(도경원 PM: 「서류마다 하이닉스의 P/O가
       없는 경우도 있어」).
    """
    import re
    rows = [r for r in item_rows(name, ex) if r.get("출처") == "CI"]
    n = len(rows)
    got = {c: sum(1 for r in rows if str(r.get(c, "")).strip()) for c in CORE3}
    all3 = sum(1 for r in rows if all(str(r.get(c, "")).strip() for c in CORE3))
    if not (doc_text or "").strip():
        where = "스캔 (글자층 없음)"
    else:
        miss = [c for c in CORE3 if not got[c] and not re.search(CORE3_HEAD[c], doc_text)]
        where = ("전부 있음" if not miss else ", ".join(miss) + " 열이 서류에 없음")
    return {"케이스": name, "인보이스 줄": n,
            "Purchasing document": f"{got['Purchase Order']}/{n}",
            "Material": f"{got['Material']}/{n}",
            "B/L quantity": f"{got['B/L quantity']}/{n}",
            "셋 다 있는 줄": f"{all3}/{n}", "서류에 열이 있나": where}


def req_coverage(rows):
    """항목마다 몇 행이 찼는지(빈칸 진단). 두 가지 분모로 낸다. 정확도가 아니다(위 ITEM_SPEC 주석).

    🔴 왜 둘인가. 전체 행을 분모로 하면 「서류에 그 열이 없어서 빈 것」 까지 우리 실패로
       읽힌다. 시리얼 번호가 15.9% 로 보이는데, 그 열이 인쇄된 서류에서는 전부 뽑았다.
       받는 쪽이 판단하려면 두 숫자가 다 있어야 한다.
    """
    out = []
    for c in [s[1] for s in ITEM_SPEC] + ["INCOTERMS"]:
        got = sum(1 for r in rows if str(r.get(c, "")).strip())
        n = len(rows)
        app = [r for r in rows if (r.get("_avail") or {}).get(c) is not False]
        got_a = sum(1 for r in app if str(r.get(c, "")).strip())
        out.append({
            "항목": c,
            "추출 행": got, "전체 행": n,
            "전체 기준": f"{got / n * 100:.1f}%" if n else "-",
            "열이 있는 행": len(app),
            "열 기준": f"{got_a / len(app) * 100:.1f}%" if app else "해당 열 없음",
        })
    return out


def invoice_rows(name, ex):
    per = {}
    for x in (ex.get("ci_lines") or []):
        if not isinstance(x, dict):
            continue
        k = str(x.get("invoice_no") or "")
        per[k] = (per.get(k) or 0) + (docpipe.num(x.get("amount")) or 0)
    cnt = {}
    for x in (ex.get("ci_lines") or []):
        if isinstance(x, dict):
            k = str(x.get("invoice_no") or "")
            cnt[k] = cnt.get(k, 0) + 1
    out = []
    tots = [x for x in (ex.get("invoice_totals") or []) if isinstance(x, dict)]
    for t in tots:
        inv = str(t.get("invoice_no") or "")
        tv = docpipe.num(t.get("total"))
        ssum = per.get(inv)
        out.append({"케이스": name, "Invoice No.": inv, "총액": _s(tv),
                    "통화": _s(t.get("currency")), "줄 수": cnt.get(inv, 0),
                    "줄 금액 합": "" if ssum is None else round(ssum, 2),
                    "차이": "" if (tv is None or ssum is None) else round(ssum - tv, 2)})
    for inv in per:
        if not any(str(t.get("invoice_no") or "") == inv for t in tots):
            out.append({"케이스": name, "Invoice No.": inv, "총액": "", "통화": "",
                        "줄 수": cnt.get(inv, 0), "줄 금액 합": round(per[inv], 2), "차이": ""})
    return out


def review_row(name, rec, rows):
    v = rec.get("verify")
    if v is None and isinstance(rec.get("case"), dict):
        v = rec["case"].get("verify")
    v = v or []
    fails = [k for g, k, *_ in v if g == "FAIL"]
    warns = [k for g, k, *_ in v if g == "WARN"]
    return {
        "케이스": name, "FAIL": len(fails), "WARN": len(warns),
        "인보이스 줄": sum(1 for r in rows if r["출처"] == "CI"),
        "포장명세서 줄": sum(1 for r in rows if r["출처"] == "PL"),
        "PO 빈 줄": sum(1 for r in rows if not str(r["Purchase Order"]).strip()),
        "품목 코드 빈 줄": sum(1 for r in rows if not str(r["Material"]).strip()
                                              and not str(r["Vendor Part No."]).strip()),
        "FAIL 항목": ", ".join(fails), "WARN 항목": ", ".join(warns), "실패": "",
    }


def write_tables(tables, path):
    """이미 만들어진 행들을 엑셀로 적는다.

    tables: {"head": [행], "items": [행], "inv": [행], "review": [행]}. 행은 열 이름을 열쇠로
    하는 사전이다. 추출 결과에서 바로 만들 수도 있고(write_xlsx), 사람이 화면에서 고친 값을
    받아서 만들 수도 있다(tools/hynix_demo.py). 고친 값이 안 담기는 내보내기는 쓸모가 없다.
    """
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter

    # 통화 열이 생기기 전에 읽은 작업의 줄은 Currency 가 비어 온다. 인보이스 표의 통화로 채운다
    add_currency(tables.get("items"), tables.get("inv"))
    wb = openpyxl.Workbook()
    hd = wb.active; hd.title = "BL Header"; hd.append(GERP_HEAD_COLS)
    it = wb.create_sheet("BL Item"); it.append(ITEM_COLS)
    iv = wb.create_sheet("인보이스"); iv.append(INV_COLS)
    c3 = wb.create_sheet("핵심 3항목"); c3.append(CORE3_COLS)
    rq = wb.create_sheet("Main 항목"); rq.append(MAIN_COLS)
    sb = wb.create_sheet("Sub 항목"); sb.append(SUB_COLS)
    cv = wb.create_sheet("빈칸 진단")
    cv.append(["항목", "추출 행", "전체 행", "전체 기준", "열이 있는 행", "열 기준"])
    df = wb.create_sheet("항목 정의"); df.append(SPEC_COLS)
    rv = wb.create_sheet("검토"); rv.append(REVIEW_COLS)
    rd = wb.create_sheet("읽는 법")

    yellow = PatternFill("solid", fgColor="FFF2CC")   # 자동으로 못 잇는 줄
    grey = PatternFill("solid", fgColor="EDEDED")     # GERP 에서 채우는 열
    gap_rows = set()
    n_gap = 0

    for r in tables.get("head") or []:
        hd.append([r.get(c, "") for c in GERP_HEAD_COLS])
    for r in tables.get("items") or []:
        it.append([r.get(c, "") for c in ITEM_COLS])
        if not str(r.get("Purchase Order", "")).strip() or (not str(r.get("Material", "")).strip()
                                                            and not str(r.get("Vendor Part No.", "")).strip()):
            n_gap += 1
            gap_rows.add(it.max_row)
            for cell in it[it.max_row]:
                cell.fill = yellow
    for r in tables.get("inv") or []:
        iv.append([r.get(c, "") for c in INV_COLS])
    for r in tables.get("review") or []:
        rv.append([r.get(c, "") for c in REVIEW_COLS])
    for r in tables.get("core3") or []:
        c3.append([r.get(c, "") for c in CORE3_COLS])
    req = tables.get("req") or []
    if not req:
        # 화면에서 고친 품목 줄만 받은 경우(고객 화면 엑셀). 고친 값이 Main 시트에도 그대로 가야 한다
        req = [dict({"케이스": r.get("케이스", ""), "Invoice No.": r.get("Invoice No.", ""), "줄": r.get("줄", "")},
                    **{label: _s(r.get(col)) for _, label, col, *_ in ITEM_SPEC})
               for r in tables.get("items") or [] if r.get("출처", "CI") == "CI"]
    warn = [s[1] for s in ITEM_SPEC if s[4]]            # 비면 사람이 봐야 하는 항목
    for r in req:
        rq.append([r.get(c, "") for c in MAIN_COLS])   # _avail 은 열 목록에 없어 안 나간다
        sb.append([r.get(c, "") for c in SUB_COLS])
        for sheet, cols in ((rq, MAIN_COLS), (sb, SUB_COLS)):
            for c in warn:
                if c in cols and not str(r.get(c, "")).strip():
                    sheet.cell(row=sheet.max_row, column=cols.index(c) + 1).fill = yellow
    for r in req_coverage(req):
        cv.append([r["항목"], r["추출 행"], r["전체 행"], r["전체 기준"], r["열이 있는 행"], r["열 기준"]])
    for tier, label, col, how, w, note in ITEM_SPEC:
        df.append([tier, label, col, COMPARE_NAME[how], "예" if w else "", note])

    # GERP 가 채우는 열은 바탕을 달리해서 빈칸이 실수로 안 보이게 한다.
    # 노란 줄은 덮지 않는다. 사람이 먼저 봐야 할 표시가 그쪽이다.
    for c in GERP_ONLY:
        col = get_column_letter(ITEM_COLS.index(c) + 1)
        for cell in it[col]:
            if cell.row > 1 and cell.row not in gap_rows:
                cell.fill = grey

    rd.append(["시트", "열", "어디서 오나", "설명"])
    for row in ORIGIN:
        rd.append(list(row))
    rd.append([])
    for n in NOTES:
        rd.append(["", "", "", n])

    widths = {"BL Header": 18, "BL Item": 16, "인보이스": 18, "핵심 3항목": 20, "Main 항목": 18,
              "Sub 항목": 16, "빈칸 진단": 18, "항목 정의": 18, "검토": 16, "읽는 법": 22}
    for sheet in (hd, it, iv, c3, rq, sb, cv, df, rv, rd):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        for i, _ in enumerate(sheet[1], 1):
            sheet.column_dimensions[get_column_letter(i)].width = widths[sheet.title]
    rd.column_dimensions["D"].width = 64
    df.column_dimensions["F"].width = 72
    for sheet, col in ((rd, 4), (df, 6)):
        for row in sheet.iter_rows(min_col=col, max_col=col):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)
    return {"items": len(tables.get("items") or []), "gaps": n_gap}


def tables_for(recs):
    """추출 결과 목록에서 네 시트의 행을 만든다."""
    t = {"head": [], "items": [], "inv": [], "review": [], "req": [], "core3": []}
    n_case = 0
    for rec in recs:
        name = case_name(rec)
        ex = extraction(rec)
        if not ex:
            t["review"].append({"케이스": name, "실패": str(rec.get("error") or "빈 결과")[:120]})
            continue
        n_case += 1
        ex = _fill_from_docs(name, ex)
        t["core3"].append(core3_rows(name, ex, _doc_text(name)))   # 「핵심 3항목」 시트
        t["head"].append(head_row(name, ex))
        rows = item_rows(name, ex)
        t["items"].extend(rows)
        t["inv"].extend(invoice_rows(name, ex))
        t["req"].extend(req_rows(name, ex, field_available(_doc_text(name))))
        t["review"].append(review_row(name, rec, rows))
    t["cases"] = n_case
    return t


def write_xlsx(recs, path):
    t = tables_for(recs)
    s = write_tables(t, path)
    return {"cases": t["cases"], "items": s["items"], "gaps": s["gaps"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="하이닉스 판 추출 결과를 GERP 품목 표로 낸다.")
    ap.add_argument("--pred", required=True, help="docpipe --save 가 남긴 JSON 파일이나 폴더")
    ap.add_argument("--out", required=True, help="xlsx 경로. 저장소 밖")
    ap.add_argument("--docs", help="원본 케이스 폴더. 주면 서류 글을 읽어 원산지 보정을 건다")
    a = ap.parse_args(argv)
    global DOC_ROOT
    if a.docs: DOC_ROOT = os.path.abspath(os.path.expanduser(a.docs))
    recs = load_preds(a.pred)
    out = os.path.abspath(os.path.expanduser(a.out))
    s = write_xlsx(recs, out)
    print(f"케이스 {s['cases']}건, 품목 줄 {s['items']}줄 "
          f"(PO 또는 품목 코드 미추출 {s['gaps']}행) → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
