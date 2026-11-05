# -*- coding: utf-8 -*-
"""하이닉스 판(h1) 추출 결과를 GERP 품목 표 모양의 엑셀로 낸다.

    python3 tools/docpipe.py --bundle 케이스=a.pdf --prompt h1 --claude-cli --save out.json
    python3 tools/gerp_xlsx.py --pred out.json --out work/out/gerp.xlsx

시트가 다섯이다.
  BL Header   선적 한 건의 헤더. GERP 「Change Bill of Lading: General1 (China)」 윗부분.
  BL Item     품목 줄. 같은 화면 아랫부분의 B/L Item Update 표.
  인보이스    묶음에 들어온 인보이스마다 번호와 총액. 여러 장이면 여기서 갈린다.
  검토        케이스마다 FAIL 과 WARN, PO 나 품목 코드가 빈 줄 수.
  읽는 법     어느 열이 서류에서 오고 어느 열이 GERP 에서 오는지, 그리고 잇는 규칙.

🔴 왜 이 모양인가. 받는 쪽이 이 화면에 넣는다. 우리 SO 업로드 양식(60열)은 KEROL 이
   받는 모양이고 하이닉스는 KEROL 을 안 쓴다. 같은 추출 결과라도 표 모양이 다르면
   사람이 손으로 옮긴다. 그러면 자동화한 자리가 도로 사람 손으로 온다.

🔴 잇는 규칙을 한 곳에 모은다. 서류에 인쇄된 코드 열이 다섯이다(Sap Code, Po No.,
   Po Item No., SK Hynix I/V No., HIOS No.). 이 중 무엇이 GERP 의 Material 인지는
   하이닉스가 정한다. 그래서 추출은 인쇄된 이름 그대로 받고, GERP 열로 잇는 일은
   GERP_MAP 한 자리에서 한다. 상대가 「Material 은 I/V No. 입니다」 라고 하면 이 표의
   한 줄만 고친다. 다섯 열을 표에 그대로 같이 실어서 상대가 눈으로 확인할 수 있게 한다.

결과 파일에는 서류 값이 들어가므로 저장소 밖에 둔다.
"""
import sys

if sys.version_info < (3, 7):
    sys.exit("파이썬 3.7 이상이 필요합니다.")

import argparse, json, os

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
# 파일명: 이 줄을 읽은 PDF. 앱 밖에서 원본과 대조하려고 둔다(wuxi_engine.payload 가 채운다)
EXTRA_ITEM_COLS = (["케이스", "파일명", "Invoice No.", "출처", "줄", "품명", "단위"]
                   + [c for c, _ in CODE_COLS]
                   + ["Case No.", "순중량(kg)", "총중량(kg)", "HS", "원산지"])
ITEM_COLS = GERP_ITEM_COLS + EXTRA_ITEM_COLS

# 🔴 하이닉스 우시 물류팀이 ’26.09.30 에 확정해 보낸 요구 항목 열 개다(임지원 PM 메일).
#    이름과 순서를 메일 그대로 둔다. 우리 쪽 이름으로 바꾸면 받는 쪽이 대조를 못 한다.
#    「없는 경우 존재」 라고 적힌 셋(Material No, Sap Code, Serial Number)은 빈칸이 정상이다.
REQ_MAP = [
    ("P/O Number", "po_no"),
    ("Material No", "material_code"),
    ("SK HYNIX Sap Code", "sap_code"),
    ("SERIAL NUMBER", "serial_no"),
    ("ITEM NAME", "description"),
    ("QUANTITY", "qty"),
    ("UNIT", "unit"),
    ("UNIT PRICE", "unit_price"),
    ("AMOUNT", "amount"),
    ("COUNTRY OF ORIGIN", "country_of_origin"),
]
# 🔴 하이닉스가 GERP I/F 에 **추가**해 달라는 항목은 셋이다(’26.09.30 도경원 PM 메일,
#    「GERP Mapping 업무 구조 및 개선검토 260922」 1장). 나머지 일곱은 그 셋을 고르기 위해
#    같이 뽑아 두는 것이다. 그래서 충족률을 셋만 따로 낸다. 열한 개를 평균 내면 제일
#    중요한 자리가 묻힌다.
# 🔴 서류에 그 열이 인쇄돼 있는지 보는 낱말. 「우리가 못 읽은 것」 과 「서류에 없는 것」 을
#    가르는 데 쓴다. 둘을 같은 분모에 넣으면 SERIAL NUMBER 가 15.9% 로 보인다. 실제로는
#    그 열이 있는 서류에서는 전부 뽑았고, 일곱 건 중 한 건에만 그 열이 있었다.
REQ_HEAD = {
    "P/O Number": r"(?i)p\s*/?\s*o\.?\s*(no|number)|purchase\s*order|\bpo\s*no\b",
    "Material No": r"(?i)\bmaterial\s*(no|code|number)\b|物料代码",
    "SK HYNIX Sap Code": r"(?i)sap\s*code",
    "SERIAL NUMBER": r"(?i)serial\s*(no|number)|\bs\s*/\s*n\b|序列号",
    "ITEM NAME": r"(?i)description|commodity|item\s*name|品名",
    "QUANTITY": r"(?i)\bq[\'’]?ty\b|quantity|수량|数量",
    "UNIT": r"(?i)\bunit\b|\bea\b|\bpcs\b|\bset\b|\bqty\s*/\s*unit\b",
    "UNIT PRICE": r"(?i)unit\s*price|단가",
    "AMOUNT": r"(?i)\bamount\b|금액",
    "COUNTRY OF ORIGIN": r"(?i)origin|원산지|原产国|made\s+in",
    "INCOTERMS": r"(?i)incoterm|\bfob\b|\bexw\b|\bcif\b|\bddp\b|\bdap\b|\bcfr\b",
}


def field_available(doc_text):
    """이 서류에 요구 항목별 열이 인쇄돼 있나. 글자가 없으면 판단하지 않는다(None)."""
    import re
    if len((doc_text or "").strip()) < 200:
        return {c: None for c in REQ_HEAD}
    return {c: bool(re.search(p, doc_text)) for c, p in REQ_HEAD.items()}


CORE3 = ["Purchase Order", "Material", "B/L quantity"]
CORE3_HEAD = {"Purchase Order": r"(?i)p\s*/?\s*o\.?\s*(no|number)|purchas",
              "Material": r"(?i)\bmaterial\b|sap\s*code|item\s*code",
              "B/L quantity": r"(?i)\bq[\'’]?ty\b|quantity|수량"}
CORE3_COLS = ["케이스", "인보이스 줄", "Purchasing document", "Material", "B/L quantity",
              "셋 다 있는 줄", "서류에 열이 있나"]

# 보충한 값은 출처를 같이 낸다. 인보이스에서 읽은 값과 통관서류에서 가져온 값은 근거가 다르다.
REQ_COLS = ["케이스", "Invoice No.", "줄"] + [c for c, _ in REQ_MAP] + ["INCOTERMS", "보충 출처", "미추출 사유"]

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
    ("BL Item", "Material", "서류", "인보이스의 Sap Code 열. 미인쇄 시 Material Code 열 적용. GERP Material 대응 열 확정 시 매핑만 변경"),
    ("BL Item", "Vendor Part No.", "서류", "인보이스의 HIOS No. 열. 미인쇄 시 Part No. 열 적용"),
    ("BL Item", "B/L quantity", "서류", "인보이스 행의 수량"),
    ("BL Item", "Invoice amount", "서류", "인보이스 행의 금액"),
    ("BL Item", "Net Price", "서류", "인보이스 행의 단가"),
    ("BL Item", "파일명", "앱", "이 행을 읽은 PDF 파일 이름. 파일이 여럿이면 원본에서 이 행이 찍힌 파일, 찾지 못하면 묶음의 파일 전부"),
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
    "서류에 인쇄된 코드 열 전체를 병기합니다. GERP 의 Material 및 Vendor Part No. 대응 열 확정 시 매핑만 변경합니다.",
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

    rows = []
    for i, x in enumerate(ci, 1):
        p = take(x)
        row = {c: pick(x, MAP_KEY[c]) for c in GERP_ITEM_COLS}
        row.update({
            "케이스": name, "Invoice No.": _s(x.get("invoice_no")), "출처": "CI",
            "줄": x.get("seq") if x.get("seq") is not None else i,
            "품명": _s(x.get("description")), "단위": _s(x.get("unit")),
            "Case No.": _s(p.get("case_no")),
            "순중량(kg)": _s(p.get("net")), "총중량(kg)": _s(p.get("gross")),
            "HS": _s(x.get("hs_code")), "원산지": _s(x.get("country_of_origin")),
        })
        row.update({c: _s(x.get(k)) for c, k in CODE_COLS})
        row.update({c: _s(x.get(k)) for c, k in REQ_MAP})   # 고객이 쓰는 이름
        # 어디서 온 값인지 화면이 표시할 수 있게 같이 싣는다
        srcs = []
        if x.get("_po_from"): srcs.append("P/O " + x["_po_from"])
        if x.get("_sap_from"): srcs.append("SAP " + x["_sap_from"])
        if x.get("_mat_from"): srcs.append("자재 " + x["_mat_from"])
        if x.get("_org_from"): srcs.append("원산지 " + x["_org_from"])
        row["_src"] = ", ".join(srcs)
        row["_unver"] = bool(x.get("_unverified"))
        rows.append(row)
    for j, x in enumerate(pl):
        if j in used:
            continue
        row = {c: pick(x, MAP_KEY[c]) for c in GERP_ITEM_COLS}
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
        row.update({c: _s(x.get(k)) for c, k in REQ_MAP})   # 고객이 쓰는 이름
        rows.append(row)
    return rows


def req_rows(name, ex, avail=None):
    """고객 요구 항목 열 개를 인보이스 줄마다 한 행으로 낸다. INCOTERMS 는 선적 단위라 줄마다 같다."""
    out = []
    for i, x in enumerate(ex.get("ci_lines") or [], 1):
        if not isinstance(x, dict):
            continue
        r = {"케이스": name, "Invoice No.": _s(x.get("invoice_no")),
             "줄": x.get("seq") if x.get("seq") is not None else i,
             "INCOTERMS": _s(ex.get("incoterms"))}
        for col, key in REQ_MAP:
            r[col] = _s(x.get(key))
        src = []
        if x.get("_po_from"): src.append(f"P/O: {x['_po_from']}")
        if x.get("_sap_from"): src.append(f"SAP Code: {x['_sap_from']}")
        if x.get("_mat_from"): src.append(f"Material No: {x['_mat_from']}")
        if x.get("_org_from"): src.append(f"원산지: {x['_org_from']}")
        r["보충 출처"] = ", ".join(src)
        # 🔴 빈칸을 그냥 두지 않는다. 왜 비었는지 적어야 받는 쪽이 판단할 수 있다.
        #    「우리가 못 읽은 것」 과 「서류에 없는 것」 은 다른 말이다.
        why = []
        if x.get("_dropped"):
            why.append("원문 대조 실패로 제거: " + ", ".join(x["_dropped"][:2]))
        av = avail or {}
        blanks = [c for c in ("P/O Number", "Material No", "SK HYNIX Sap Code", "SERIAL NUMBER")
                  if not str(r.get(c, "")).strip()]
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
    """요구 항목마다 몇 행이 찼는지. 두 가지 분모로 낸다.

    🔴 왜 둘인가. 전체 행을 분모로 하면 「서류에 그 열이 없어서 빈 것」 까지 우리 실패로
       읽힌다. SERIAL NUMBER 가 15.9% 로 보이는데, 그 열이 인쇄된 서류에서는 전부 뽑았다.
       받는 쪽이 판단하려면 두 숫자가 다 있어야 한다.
    """
    out = []
    for c in [x for x, _ in REQ_MAP] + ["INCOTERMS"]:
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

    wb = openpyxl.Workbook()
    hd = wb.active; hd.title = "BL Header"; hd.append(GERP_HEAD_COLS)
    it = wb.create_sheet("BL Item"); it.append(ITEM_COLS)
    iv = wb.create_sheet("인보이스"); iv.append(INV_COLS)
    c3 = wb.create_sheet("핵심 3항목"); c3.append(CORE3_COLS)
    rq = wb.create_sheet("요구항목"); rq.append(REQ_COLS)
    cv = wb.create_sheet("항목별 충족")
    cv.append(["항목", "추출 행", "전체 행", "전체 기준", "열이 있는 행", "열 기준"])
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
    for r in req:
        rq.append([r.get(c, "") for c in REQ_COLS])   # _avail 은 REQ_COLS 에 없어 안 나간다
        # 「없는 경우 존재」 라고 하신 셋이 아닌데 빈 칸이면 사람이 봐야 한다
        for c in ("P/O Number", "ITEM NAME", "QUANTITY", "UNIT PRICE", "AMOUNT"):
            if not str(r.get(c, "")).strip():
                rq.cell(row=rq.max_row, column=REQ_COLS.index(c) + 1).fill = yellow
    for r in req_coverage(req):
        cv.append([r["항목"], r["추출 행"], r["전체 행"], r["전체 기준"], r["열이 있는 행"], r["열 기준"]])

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

    widths = {"BL Header": 18, "BL Item": 16, "인보이스": 18, "핵심 3항목": 20,
               "요구항목": 18, "항목별 충족": 18, "검토": 16, "읽는 법": 22}
    for sheet in (hd, it, iv, c3, rq, cv, rv, rd):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        for i, _ in enumerate(sheet[1], 1):
            sheet.column_dimensions[get_column_letter(i)].width = widths[sheet.title]
    rd.column_dimensions["D"].width = 64
    for row in rd.iter_rows(min_col=4, max_col=4):
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
