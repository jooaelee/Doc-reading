# -*- coding: utf-8 -*-
"""확인 화면과 가상 KEROL 화면을 만든다. 서버(`tools/ui_server.py`)가 띄운다.

    python3 tools/confirm_ui.py --pred work/out/batch4 --compare work/out/batch3 \\
        --root work/docs_raw --out work/out/ui

  confirm.html     왼쪽 케이스 목록, 가운데 항목(필수와 선택, 검토 표시), 오른쪽 원본 PDF(pdf.js).
                   값을 누르면 PDF 의 그 자리로 확대해 간다. "새 서류 뽑기" 탭은 PDF 를 올려 바로 뽑는다.
  kerol_mock.html  KEROL 의 HAWB List (In) 화면을 흉내 낸 것. 확인 화면에서 보내면 그리드에 행이 꽂힌다.

값이 들어가므로 저장소 밖에 낸다. 두 파일은 브라우저의 localStorage 로 값을 주고받는다.
"""
import sys

if sys.version_info < (3, 7):
    sys.exit("파이썬 3.7 이상이 필요합니다.")

import argparse, collections, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import docpipe
from so_upload import load_preds, differing, SO_KEYS

# SO 행을 만들려면 있어야 하는 칸. 비면 붉게 표시하고 사람이 채운다.
REQUIRED = ["hawb", "shipper_name", "consignee_name", "origin", "destination", "awb_pieces", "awb_gross",
            "invoice_no", "ci_total", "currency", "incoterms"]
OPTIONAL = ["mawb", "flight1", "customer_po", "item_desc", "pl_packages", "package_unit", "awb_chargeable",
            "pl_gross", "pl_net", "ci_gross", "ci_net", "awb_declared_value", "dims_cm", "agent_iata_code",
            "freight_term", "measurement_cbm"]
LABELS = {"hawb": "HBL No", "mawb": "MBL No", "shipper_name": "Shipper", "consignee_name": "Consignee",
          "origin": "Port of Loading", "destination": "Port of Discharge", "awb_pieces": "Package Qty.", "awb_gross": "Gross Weight",
          "invoice_no": "Ref. No. (INV)", "ci_total": "Invoice Amt", "currency": "Invoice Cur", "incoterms": "Incoterms",
          "flight1": "Flight / Vessel", "customer_po": "Customer Order No", "item_desc": "Item Desc", "pl_packages": "P/L Packages",
          "package_unit": "Package Unit", "awb_chargeable": "Chargeable Weight", "pl_gross": "P/L Gross Weight", "pl_net": "P/L Net Weight",
          "ci_gross": "C/I Gross Weight", "ci_net": "C/I Net Weight", "awb_declared_value": "Declared Value", "dims_cm": "Dimension (cm)",
          "agent_iata_code": "Agent IATA Code", "freight_term": "Freight Term", "measurement_cbm": "Measurement (CBM)",
          "ci_lines": "C/I Lines", "pl_lines": "P/L Lines", "shipper_address": "Shipper Address",
          "consignee_address": "Consignee Address", "agent_account_no": "Agent Account No", "lca_lines": "LCA Lines"}
# 검증 규칙 키가 가리키는 칸. 검토 표시를 어느 칸에 붙일지 정한다.
RULE_FIELDS = {"ci_total": ["ci_total", "ci_lines"], "gross_weight": ["awb_gross", "pl_gross", "ci_gross"], "net_weight": ["pl_net", "ci_net"],
               "pl_vs_ci_qty": ["pl_lines", "ci_lines"], "pkg_vs_pieces": ["pl_packages", "awb_pieces"], "chargeable": ["awb_chargeable"],
               "declared_value": ["awb_declared_value", "ci_total"], "mawb_check_digit": ["mawb"], "mawb_format": ["mawb"],
               "hawb_vs_mawb": ["hawb", "mawb"], "agent_iata_code": ["agent_iata_code"], "agent_code_vs_account": ["agent_iata_code"],
               "incoterms_vs_freight": ["incoterms", "freight_term"], "measurement_cbm": ["measurement_cbm"], "package_unit": ["package_unit"],
               "pl_line_gross": ["pl_gross", "pl_lines"], "pl_line_net": ["pl_net", "pl_lines"], "freight_term": ["freight_term"], "awb_charges": ["awb_total"]}


RULE_LABELS = {"gross_weight": "총중량 불일치", "net_weight": "순중량 불일치", "ci_total": "인보이스 합계 불일치",
               "pl_vs_ci_qty": "수량 불일치", "pkg_vs_pieces": "포장 수 불일치", "chargeable": "청구 중량 확인",
               "declared_value": "신고가액 불일치", "mawb_check_digit": "MAWB 검증 숫자 오류", "mawb_format": "MAWB 형식 오류",
               "hawb_vs_mawb": "HBL과 MBL 번호 동일", "agent_iata_code": "대행사 코드 확인", "agent_code_vs_account": "대행사 코드와 계정 혼동",
               "incoterms_vs_freight": "인코텀즈와 운임 조건 불일치", "measurement_cbm": "용적 확인", "package_unit": "포장 단위 코드 없음",
               "pl_line_gross": "PL 품목별 총중량 합계 불일치", "pl_line_net": "PL 품목별 순중량 합계 불일치", "freight_term": "운임 조건 확인",
               "awb_charges": "운임 합계 불일치", "lca_fx": "환율 계산 불일치", "currency": "통화 확인"}


WEIGHT_SRC = {"pl_gross": "PL", "ci_gross": "CI", "awb_gross": "AWB", "pl_net": "PL", "ci_net": "CI"}
# 검증 규칙의 원래 문장(docpipe.verify)은 개발 기록용이라 변수 이름과 구어가 섞여 있다. 화면에는 아래 꼴로 바꿔 낸다.
MESSAGE_FORMS = [
    (r"^PL 포장 (\S+) vs AWB 피스 (\S+)$", r"PL 포장 \1, {doc} 수량 \2"),
    (r"^PL (\S+) vs CI (\S+)\. 인보이스가 한 덩어리로 청구했습니다$", r"PL \1, CI \2 (인보이스 일괄 청구)"),
    (r"^PL (\S+) vs CI (\S+)$", r"PL \1, CI \2"),
    (r"^AWB (\S+) vs CI (\S+)$", r"{doc} \1, CI \2"),
    (r"^줄 합 (\S+) vs 총액 (\S+)$", r"품목 합계 \1, 인보이스 총액 \2"),
    (r"^줄 합 (\S+) vs 총계 (\S+)$", r"품목 합계 \1, 총계 \2"),
    (r"^칸 합 (\S+) vs 합계 (\S+)$", r"항목 합계 \1, 운임 총액 \2"),
    (r"^치수에서 (\S+) vs 적힌 값 (\S+)$", r"치수 계산값 \1, 기재값 \2"),
    (r"^(\S+)×(\S+)=(\S+) ≠ (\S+)$", r"수량 \1 × 단가 \2 = \3, 기재 금액 \4"),
    (r"^용적 (\S+), 실중량 (\S+) 인데 chargeable (\S+)$", r"용적 중량 \1, 실중량 \2, 청구 중량 \3"),
    (r"^chargeable (\S+) < gross (\S+)$", r"청구 중량 \1이 총중량 \2보다 작음"),
    (r"^점이 천 단위 구분자로 보임\(×1000 이면 맞음\): (.*)$", r"천 단위 구분점으로 추정 (1,000배 적용 시 일치): \1"),
    (r"^환율 곱 불일치: (.*)$", r"\1"),
    (r"^단위 낱말을 코드로 못 바꿨다: (.*)\. 사람이 고른다$", r"코드 변환 실패 \1 (직접 선택 필요)"),
    (r"^IATA 코드 자릿수가 낯설다\((\d+)자리\): (.*)$", r"IATA 코드 \1자리, 자릿수 확인 필요: \2"),
    (r"^IATA 코드 자리에 숫자가 아닌 값: (.*)$", r"숫자 외 문자 포함: \1"),
    (r"^IATA 코드와 계정 번호가 같다: (.*)$", r"동일 번호: \1"),
    (r"^PREPAID 나 COLLECT 가 아닌 값: (.*)$", r"PREPAID, COLLECT 외 값: \1"),
    (r"^(\S+) 인데 (\S+) 다\. (\S+) 가 보통이다$", r"\1, \2 (통상 \3)"),
    (r"^하우스와 마스터가 같은 번호다: (.*)$", r"\1"),
    (r"^검증 숫자가 안 맞지만 원문에 그대로 있다: (.*)$", r"원문 표기와 동일, 검증 숫자 불일치: \1"),
    (r"^(\S+) → (\S+)$", r"\1 (계산상 \2)"),
    (r"^MAWB 자릿수 (\d+) \(11 이어야\)$", r"\1자리 (11자리 필요)"),
]


def rule_text(g, k, m, mode="AIR"):
    """검증 결과 한 줄을 화면 문구로 바꾼다. 수치는 그대로 두고 규칙 이름과 비교값만 남긴다."""
    label = RULE_LABELS.get(k)
    if not label and re.match(r"ci_line\d+", k):
        label = f"인보이스 {k[7:]}번째 품목 금액 불일치"
    m = str(m)
    doc = "B/L" if mode == "SEA" else "AWB"
    if m.startswith("원문에 없는 값"):
        return f"{LABELS.get(k, k)}: 원문에서 확인되지 않음"
    if m.startswith("텍스트에 없음"):
        return f"{LABELS.get(k, k)}: 스캔 이미지 판독값 (원문 대조 불가)"
    if k in ("gross_weight", "net_weight") and "=" in m:
        tail = " (운송장 재계량 차이로 추정)" if "재계량" in m else ""
        parts = re.findall(r"(\w+)=([\d.]+)", m)
        return f"{label}: " + ", ".join(f"{WEIGHT_SRC.get(a, a).replace('AWB', doc)} {float(b):,g} kg" for a, b in parts) + tail
    for pat, rep in MESSAGE_FORMS:
        if re.match(pat, m):
            m = re.sub(pat, rep.replace("{doc}", doc), m)
            break
    return f"{label or LABELS.get(k, k)}: {m}"


def search_variants(k, v):
    if v is None or v == "" or isinstance(v, (list, dict)):
        return []
    s = str(v).strip()
    out = [s]
    if isinstance(v, (int, float)) or re.fullmatch(r"-?\d+(\.\d+)?", s):
        f = float(v)
        out += [f"{f:,.2f}", f"{f:,.0f}" if f == int(f) else f"{f:,}", f"{f:g}", f"{int(f):,}" if f == int(f) else s, f"{f:.3f}",
                f"{f:.2f}", f"{f:.1f}", f"{f:,.3f}", f"{f:,.1f}"]
        if k in ("awb_pieces", "pl_packages"):
            # 한 자리 수는 그대로 찾으면 쪽마다 걸린다. 포장 낱말과 붙은 꼴만 찾는다
            n = int(f)
            out = [f"{n} {u}" for u in ("PKG", "PKGS", "PALLET", "PALLETS", "PLT", "PLTS", "PACKAGES", "PACKAGE", "CARTONS", "CTNS", "CASES", "DRUMS", "RACKS", "CRATES")] + \
                  [f"({n} PALLET", f"({n} PACKAGE", f"{n}PKG", f"{n} PCS"] + ([str(n)] if n >= 10 else [])
    if k in ("hawb", "mawb", "customer_po", "invoice_no", "agent_iata_code"):
        out += [re.sub(r"[\s\-]", "", s), s.replace("-", " ")]
        d = re.sub(r"\D", "", s)
        if k == "mawb" and len(d) == 11: out += [d, d[:3] + "-" + d[3:], d[:3] + " " + d[3:7] + " " + d[7:]]
    if k in ("shipper_name", "consignee_name", "item_desc", "origin", "destination") and len(s) > 14:
        out.append(s[:14])
    # 🔴 줄 품명은 길고 줄바꿈이 끼어 통째로는 62% 밖에 안 걸린다. 앞 토막을 같이 대면 83% 다.
    #    품명이 이 판에서 제일 중요한 칸인데 짚어 주지 못하면 「인식이 안 됐다」 로 읽힌다.
    if k.endswith(":ITEM NAME") and len(s) > 10:
        out += [s[:24], s[:16], s[:10]]
    seen, uniq = set(), []
    for x in out:
        if x and (len(x) >= 2 or k in ("awb_pieces", "pl_packages")) and x not in seen: seen.add(x); uniq.append(x)
    return uniq


LINE_PREFER = ("CI", "PL")       # 품목 행의 근거는 인보이스와 포장명세서다


def is_line_key(k):
    return bool(re.match(r"^L\d+:", str(k)))


def line_col(k):
    """줄 칸 이름. 줄 칸이 아니면 빈 글."""
    m = re.match(r"^L\d+:(.+)$", str(k))
    return m.group(1) if m else ""


def page_kind_map(pdfs):
    """쪽 종류만 먼저 센다([[pdf 0 의 쪽 종류...], ...]). 글자층 분류라 OCR 없이 끝난다."""
    out = []
    for p in pdfs:
        try:
            pages = docpipe.load_pdf(p)
            for pg in pages:
                docpipe.classify(pg)
            out.append([pg.kind for pg in pages])
        except Exception:
            out.append([])
    return out


def _page_seq(docs, kinds, prefer=()):
    """훑을 (pdf 번호, 쪽 번호) 차례. prefer 에 든 종류의 쪽을 앞으로 보낸다."""
    head, tail = [], []
    for di, doc in enumerate(docs):
        if doc is None:
            continue
        for pi in range(len(doc)):
            k = kinds[di][pi] if di < len(kinds) and pi < len(kinds[di]) else ""
            (head if k in prefer else tail).append((di, pi))
    return head + tail


def highlights(pdfs, ex, keys=None, kinds=None, weak=None):
    """{칸: [[pdf 번호, 쪽, x0, y0, x1, y1], ...]} 를 pymupdf 검색으로 만든다. 못 여는 PDF 는 건너뛴다.

    🔴 품목 행은 인보이스와 포장명세서 쪽을 먼저 훑는다. 쪽 차례대로 찾으면 1쪽의 운송장이
       먼저 걸려서, 발주번호나 금액을 눌렀을 때 운송장 머리가 뜬다. 보여 줄 자리는 품목이
       인쇄된 Commercial Invoice 와 Packing List 다.
    """
    import pymupdf
    docs = []
    for p in pdfs:
        try: docs.append(pymupdf.open(p))
        except Exception: docs.append(None)
    kinds = kinds or []
    seq_all = _page_seq(docs, kinds)
    pref = {(di, pi) for di, pi in seq_all
            if di < len(kinds) and pi < len(kinds[di]) and kinds[di][pi] in LINE_PREFER}
    seq_line = _page_seq(docs, kinds, LINE_PREFER)
    out = {}
    for k in (keys or list(ex)):
        if line_col(k) in BAND_ONLY:
            continue
        v = ex.get(k)
        line = is_line_key(k)
        seq = seq_line if line else seq_all
        hits = []
        for q in search_variants(k, v):
            for di, pi in seq:
                page = docs[di][pi]
                try: rects = page.search_for(q)
                except Exception: rects = []
                for r in rects[:2]:
                    hits.append([di, pi + 1, round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)])
                if len(hits) >= 4: break
            if hits: break
        if not hits:
            continue
        # 🔴 품목 행이 인보이스가 아닌 쪽에서만 걸렸으면 아직 확정하지 않는다. 그 묶음의 표가
        #    그림일 수 있고, 그러면 OCR 이 인보이스 쪽에서 같은 값을 찾아낸다. OCR 도 못 찾으면
        #    build_case 가 이 자리를 되살린다. 빈 자리로 두는 것보다는 낫다.
        if line and pref and not any((h[0], h[1] - 1) in pref for h in hits):
            if weak is not None:
                weak[k] = hits
            continue
        out[k] = hits
    return out


def _norm(x):
    # OCR 이 자주 헷갈리는 글자는 한쪽으로 모은다. 양쪽에 똑같이 거니 맞는 값을 놓치지 않는다.
    # 🔴 Q 도 0 으로 모은다. 「Q'tz cover ring」 을 OCR 이 「0'tz」 로 읽어 그 행 품명만 비었다
    return re.sub(r"[^A-Z0-9.]", "", str(x).upper()).replace("O", "0").replace("Q", "0")


def ocr_highlights(pdfs, ex, keys, hl, out_lines=None):
    """글자층이 없는 쪽은 pymupdf 검색이 못 찾는다. OCR 줄 좌표에서 값을 찾아 같은 꼴 [pdf, 쪽, x0, y0, x1, y1] 로 보탠다.
    쪽 종류도 함께 돌려준다([[pdf 0 의 쪽 종류...], ...]). 확인 화면이 칸을 못 찾을 때 그 서류 쪽으로 가는 데 쓴다.

    🔴 인보이스와 포장명세서의 품목 표가 그림으로 찍힌 묶음이 있다. 양식 낱말만 글자층에 있고
       값은 그림이다(실측 HDLCA125503). 그 쪽은 SCAN 으로 갈리지 않아 앞 판은 건너뛰었고, 그래서
       발주번호를 누르면 글자층이 있는 1쪽 운송장이 떴다. 그림이 박힌 CI, PL 쪽도 함께 읽는다.
    """
    import pymupdf
    kinds, scan, img = [], [], []
    for di, path in enumerate(pdfs):
        pages = docpipe.load_pdf(path)
        for pg in pages:
            docpipe.classify(pg)
        kinds.append([pg.kind for pg in pages])
        scan += [(di, pg) for pg in pages if pg.kind == "SCAN"]
        img += [(di, pg) for pg in pages
                if pg.kind in LINE_PREFER and getattr(pg, "n_images", 0)]
    img = img[:8]                 # 쪽이 많은 묶음에서 시간이 튀지 않게 막는다
    if (not scan and not img) or docpipe.ocr_engine() == "none":
        return kinds
    for _, pg in img:             # ocr_pages 는 SCAN 으로 갈린 쪽만 읽는다. 읽는 동안만 표시한다
        pg.kind = "SCAN"
    d = docpipe.ocr_pages([pg for _, pg in scan + img])
    try:
        docpipe.route_scans([pg for _, pg in scan])
        for di, pg in scan:
            kinds[di][pg.no - 1] = ("AWB" if pg.sea else pg.kind) if pg.kind != "SCAN" else "SCAN"
        for di, pg in img:
            pg.kind = kinds[di][pg.no - 1]        # 원래 갈래로 되돌린다
        lines_by = {}
        for di, pg in scan + img:
            if not getattr(pg, "ocr", ""):
                continue
            # 줄 좌표가 필요하다. Vision 이면 실행 파일을 한 번 더 부르지 않고 같은 그림으로 줄을 다시 얻는다
            lines_by[(di, pg.no)] = _ocr_lines(pg.png)
        if out_lines is not None:
            # 🔴 띠 짚기가 같은 좌표를 다시 쓴다. OCR 을 두 번 돌리지 않게 여기서 넘겨준다
            for (di, pno), lines in lines_by.items():
                try:
                    r = pymupdf.open(pdfs[di])[pno - 1].rect
                except Exception:
                    continue
                out_lines[(di, pno)] = [
                    (_norm(t), round(x * r.width, 1), round(y * r.height, 1),
                     round((x + w) * r.width, 1), round((y + h) * r.height, 1))
                    for t, x, y, w, h in lines]
        # 🔴 스캔 쪽도 같은 차례를 쓴다. 품목 행은 인보이스와 포장명세서 쪽을 먼저 본다.
        def _seq(k):
            ks = sorted(lines_by, key=lambda t: 0 if (
                t[1] - 1 < len(kinds[t[0]]) and kinds[t[0]][t[1] - 1] in LINE_PREFER) else 1)
            return ks if is_line_key(k) else list(lines_by)

        for k in keys:
            if k in hl or line_col(k) in BAND_ONLY:
                continue
            vs = [_norm(q) for q in search_variants(k, ex.get(k)) if len(_norm(q)) >= 2]
            hits = []
            for di, pno in _seq(k):
                lines = lines_by[(di, pno)]
                try:
                    r = pymupdf.open(pdfs[di])[pno - 1].rect
                except Exception:
                    continue
                for text, x, y, w, h in lines:
                    t = _norm(text)
                    if any(q and q in t for q in vs):
                        hits.append([di, pno, round(x * r.width, 1), round(y * r.height, 1), round((x + w) * r.width, 1), round((y + h) * r.height, 1)])
                        break
                if len(hits) >= 2:
                    break
            if hits:
                hl[k] = hits
    finally:
        if d:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
    return kinds


def _ocr_lines(png):
    eng = docpipe.ocr_engine()
    if eng == "vision":
        import subprocess
        out = subprocess.run([docpipe.OCR_BIN, png], capture_output=True, text=True, timeout=60).stdout
        for line in out.splitlines():
            try:
                return json.loads(line).get("lines") or []
            except ValueError:
                pass
        return []
    if eng == "rapid":
        docpipe._rapid_init() if docpipe._rapid_eng is None else None
        return docpipe._rapid_one((png, 1.0))
    return []


def page_tokens(pdfs, kinds, ocr_lines=None):
    """인보이스와 포장명세서 쪽의 글 토막. (바른 글, x0, y0, x1, y1) 꼴이며 PDF 좌표다.

    🔴 글자층 낱말을 먼저 쓴다. OCR 줄을 먼저 썼더니 줄 통째가 토막 하나가 되어 수량 칸을
       가려내지 못했다. 표가 그림으로 찍혀 낱말이 없는 쪽만 OCR 줄로 메운다.
    """
    import pymupdf
    ocr_lines = ocr_lines or {}
    out = {}
    for di, path in enumerate(pdfs):
        try:
            doc = pymupdf.open(path)
        except Exception:
            continue
        for pi in range(len(doc)):
            if di >= len(kinds) or pi >= len(kinds[di]) or kinds[di][pi] not in LINE_PREFER:
                continue
            try:
                ws = doc[pi].get_text("words")
            except Exception:
                ws = []
            toks = [(_norm(w[4]), round(w[0], 1), round(w[1], 1), round(w[2], 1), round(w[3], 1))
                    for w in ws if _norm(w[4])]
            # 🔴 둘을 합친다. 양식 낱말만 글자층에 있고 값은 그림인 쪽이 있어서(실측 서류 A, B)
            #    글자층만 쓰면 단가와 수량을 통째로 놓친다. 낱말을 앞에 두고 OCR 줄을 뒤에 붙인다.
            toks = toks + (ocr_lines.get((di, pi + 1)) or [])
            if toks:
                out[(di, pi + 1)] = toks
    return out


def _row_find(row, v, near=None, every=False, cy=None, hh=None):
    """한 줄의 띠 안에서 값이 적힌 자리를 찾는다. near 가 있으면 그 x 에 가까운 것을 고른다.
    every 면 후보 전체를 고르는 차례대로 돌려준다."""
    q = _norm(v)
    if not q:
        return [] if every else None
    cand, kind = [], {}
    for t in row:
        if t[0] == q or (len(q) >= 3 and q in t[0]):
            cand.append([t[1], t[2], t[3], t[4]]); kind[id(cand[-1])] = 0
    # 🔴 세 가지 찾기를 늘 다 한다. 앞 판은 첫 찾기에서 하나라도 걸리면 멈췄는데, 그 하나가 OCR 줄
    #    (줄 통째, 위치가 위로 조금 밀린다)이면 글자층 낱말을 이어 붙인 정확한 자리를 버렸다.
    #    그래서 품명이 한 줄씩 위 칸을 짚었다.
    for i in range(len(row)):           # 천 단위 쉼표나 띄어쓰기로 토막 난 값은 이어 붙인다
        acc = ""
        for j in range(i, min(i + 8, len(row))):
            if j > i and abs(row[j][2] - row[i][2]) > 3:
                break
            acc += row[j][0]
            if acc == q and j > i:
                cand.append([row[i][1], min(row[i][2], row[j][2]), row[j][3], max(row[i][4], row[j][4])])
                kind[id(cand[-1])] = 0
                break
            if len(acc) > len(q) or not q.startswith(acc):
                break
    if len(q) >= 10:                    # 품명은 길어서 줄바꿈이 끼거나 뒤가 잘린다. 앞 토막으로 댄다
        for t in row:
            if len(t[0]) >= 8 and t[0] != q and q not in t[0] and \
                    (t[0].startswith(q[:8]) or q.startswith(t[0][:8])):
                cand.append([t[1], t[2], t[3], t[4]]); kind[id(cand[-1])] = 1
    if not cand:
        return [] if every else None
    # 🔴 띠가 이웃 줄에 조금 걸친다. 같은 값이 위아래 줄에 다 있으면(같은 발주번호) 가운데 줄의
    #    것을 먼저 쓴다. 안 그러면 두 줄이 같은 칸을 짚고 한 줄은 희게 남는다.
    def rank(b):
        dy = abs((b[1] + b[3]) / 2 - cy) / max(1.0, hh * 0.5) if cy is not None and hh else 0
        # 값 전체가 맞은 자리를 앞 토막만 맞은 자리보다 먼저 쓴다. 품명 상자가 첫 낱말만 덮었다
        return (round(dy), abs(b[0] - near) if near is not None else 0, kind.get(id(b), 0), b[2] - b[0])
    cand.sort(key=rank)
    return cand if every else cand[0]


# 열 머리글로 쓰는 이름. 값이 모두 1 처럼 짧아 값으로는 열 자리를 못 잡을 때 머리글 자리로 잡는다
HEAD_ALIAS = {
    "QUANTITY": ("QTY", "QUANTITY", "QTYEA", "QTY.", "Q.TY", "QUAN"),
    "B/L quantity": ("QTY", "QUANTITY", "QTYEA", "QTY.", "Q.TY", "QUAN"),
    "UNIT PRICE": ("UNITPRICE", "UPRICE", "PRICE"),
    "Net Price": ("UNITPRICE", "UPRICE", "PRICE"),
    "AMOUNT": ("AMOUNT", "AMT", "TOTALAMOUNT"),
    "Invoice amount": ("AMOUNT", "AMT", "TOTALAMOUNT"),
    "UNIT": ("UNIT", "UOM", "U/M", "UM"),
    "COUNTRY OF ORIGIN": ("ORIGIN", "COO", "COUNTRYOF", "MADEIN"),
}


def _head_x(tokens, col, above):
    """머리글 이름표의 가운데 x. above 보다 위쪽에서 가장 가까운 것을 쓴다."""
    names = HEAD_ALIAS.get(col)
    if not names:
        return None
    best = None
    for t in tokens:
        # 짧은 이름은 통째로 맞아야 한다. UNIT 이 UNITPRICE 머리글에 걸렸다
        # 이름표도 값과 같은 규칙으로 바른다(Q 를 0 으로). 안 그러면 Qty 머리글을 못 찾는다
        if t[4] > above or not any(t[0] == _norm(n) if len(n) <= 4 else t[0].startswith(_norm(n)) for n in names):
            continue
        if best is None or t[2] > best[2]:
            best = t
    return (best[1] + best[3]) / 2 if best else None


def line_bands(lex, hl, tokens, rows=None, kinds=None, alt=None, ocr_cell=None, ok_lines=None):
    """줄마다 그 줄이 인쇄된 띠를 잡고, 띠 안에서 각 칸을 짚는다. 채운 칸 수를 돌려준다.

    🔴 왜 필요한가. 통째 찾기는 값이 같은 줄을 가리지 못한다. 품명과 단가가 전부 같은 수리
       인보이스에서는 일곱 줄이 모두 같은 상자 네 개를 가리켰고 나머지 줄은 흰 칸으로 남았다.
       수량처럼 한두 자리 수는 쪽 아무 데나 걸려서 아예 안 찾았다. 둘 다 화면에서는 「그 줄은
       못 읽었다」 로 읽힌다. 줄의 띠를 먼저 잡고 띠 안에서만 찾으면 둘 다 풀린다.

    🔴 띠 후보가 여럿이면 가장 많이 채워지는 쪽을 쓴다. 같은 품목표가 두 번 실리고 뒤쪽에만
       단가가 적힌 인보이스가 있다. 앞쪽 표에 띠를 잡으면 단가와 금액을 못 찾는다.
    """
    import collections
    by_line = {}
    for k, v in lex.items():
        c = line_col(k)
        if not c or str(v or "").strip() == "":
            continue
        by_line.setdefault(int(re.match(r"^L(\d+):", k).group(1)), {})[c] = v
    if not by_line or not tokens:
        return 0
    # 🔴 같은 품목이 인보이스와 포장명세서에 한 줄씩 들어온다. 둘을 섞어 세면 어느 값도 「이 줄만의
    #    값」 이 아니게 되어 닻을 못 잡는다. 출처로 가른 다음 센다.
    kinds = kinds or []
    src = {n: ((rows[n].get("출처") if rows and n < len(rows) else None) or "CI") for n in by_line}
    freq = {}
    for n, cols in by_line.items():
        for c, v in cols.items():
            freq.setdefault((src[n], c), collections.Counter())[_norm(v)] += 1

    def page_kind(di, pno):
        return kinds[di][pno - 1] if di < len(kinds) and pno - 1 < len(kinds[di]) else ""

    def band_of(h):
        hgt = max(4.0, h[5] - h[3])
        return (h[0], h[1], h[3] - hgt * BAND_SPAN, h[5] + hgt * BAND_SPAN)

    def row_toks(b):
        di, pno, lo, hi = b
        return [t for t in tokens.get((di, pno)) or [] if t[2] < hi and t[4] > lo]

    bands, others = {}, {}
    for n, cols in by_line.items():
        best, cands = None, []
        for c, v in cols.items():
            if freq[(src[n], c)][_norm(v)] != 1:
                continue
            for h in (hl.get(f"L{n}:{c}") or []):
                if (h[0], h[1]) not in tokens or page_kind(h[0], h[1]) != src[n]:
                    continue
                b = band_of(h)
                row = row_toks(b)
                n_hit = sum(1 for cc, vv in cols.items() if _row_find(row, vv))
                cands.append((n_hit, b))
                if best is None or n_hit > best[0]:
                    best = (n_hit, b)
        if best and best[0] >= 2:
            bands[n] = best[1]
            # 쓰지 않은 띠. 같은 품목이 다른 표에도 실린 자리다(견적 표, 사본 쪽)
            others[n] = [b for k, b in cands if k >= max(3, 0.6 * best[0])
                         and (b[0], b[1]) == (best[1][0], best[1][1]) and abs(b[2] - best[1][2]) > 6]
    if not bands:
        return 0
    # 두 번 훑는다. 긴 값으로 열의 x 자리를 먼저 잡고, 짧은 수는 그 자리에 가까운 것을 고른다
    colx, colw, n_fill = collections.defaultdict(list), collections.defaultdict(list), 0
    ci_no = {}                               # 줄 번호 -> 인보이스 안에서 몇째 줄인가(검산 줄 이름 ci_lineN)
    for n in sorted(by_line):
        if src[n] == "CI":
            ci_no[n] = len(ci_no) + 1
    used = collections.defaultdict(set)      # 줄마다 이미 쓴 상자
    row_ocr = {}                             # 행 띠를 다시 읽은 결과
    # 🔴 GERP 열과 요구 항목 열은 같은 값을 다른 이름으로 들고 있다. 같은 칸을 짚는 것이 맞으므로
    #    상자를 나눠 갖지 않게 막는 대상에서 뺀다
    twin = {"Purchase Order": "P/O Number", "Material": "Material No", "B/L quantity": "QUANTITY",
            "Net Price": "UNIT PRICE", "Invoice amount": "AMOUNT"}
    for rnd in (0, 1):
        for n, b in sorted(bands.items()):
            di, pno, lo, hi = b
            row = row_toks(b)
            if not row:
                continue
            for c, v in sorted(by_line[n].items(), key=lambda t: t[0] in twin):
                q = _norm(v)
                if (len(q) >= 2) != (rnd == 0):
                    continue
                key = f"L{n}:{c}"
                if c in twin and hl.get(f"L{n}:{twin[c]}") and \
                        (hl[f"L{n}:{twin[c]}"][0][0], hl[f"L{n}:{twin[c]}"][0][1]) == (di, pno):
                    hl[key] = [list(hl[f"L{n}:{twin[c]}"][0])]
                    continue
                xs = sorted(colx.get(c) or [])
                near = xs[len(xs) // 2] if xs else _head_x(tokens[(di, pno)], c, lo)
                hh = (hi - lo) / (1 + 2 * BAND_SPAN)
                # 🔴 같은 상자를 두 칸이 못 갖게 하는 것은 숫자 열에만 건다. 단가와 금액이 같은 값이어도
                #    칸은 둘이다. 품목코드와 SAP 코드는 같은 값이면 같은 칸이다(한 열을 두 이름으로 부른다)
                cands = [x for x in _row_find(row, v, near, every=True, cy=(lo + hi) / 2, hh=hh)
                         if c not in HEAD_ALIAS or (round(x[0]), round(x[1])) not in used[n]]
                # 🔴 짧은 값은 같은 줄 높이와 같은 열 자리에 있는 것만 받는다. 이웃 줄의 같은 숫자(1)나 다른 열의
                #    깨진 글자(3)를 받으면, 그 줄은 남의 칸을 짚고 제 칸은 비었다.
                if len(q) <= 2:
                    cy0 = (lo + hi) / 2
                    if len(q) == 1 and xs:
                        cands = [x for x in cands if xs[0] - 8 <= x[0] <= xs[-1] + 8 or xs[0] - 8 <= x[2] <= xs[-1] + 8]
                    strict = [x for x in cands if abs((x[1] + x[3]) / 2 - cy0) <= hh * 0.7]
                    # 칸 안에 여러 줄로 적힌 행(품명 밑에 수량이 따로 적힌 것)은 띠 안이면 받는다. 다만 검산으로
                    # 확인된 줄은 열 자리로 짚을 수 있으니 엄격한 쪽을 쓴다
                    can_geo = c in ("QUANTITY", "B/L quantity") and xs and ok_lines and ci_no.get(n) in ok_lines
                    cands = strict if (strict or can_geo) else cands
                # 🔴 한 자리 수량은 쪽 전체 OCR 에서 자주 빠진다(서류 A 의 2, 1, 3). 열 자리를 아는 칸만 크게
                #    잘라 다시 읽는다. 열 자리를 모르면 하지 않는다. 엉뚱한 칸을 읽게 된다.
                if not cands and ocr_cell and len(q) <= 2 and xs:
                    try:
                        cell = ocr_cell(di, pno, (xs[0] - 24, lo, xs[-1] + 34, hi))
                        cands = [x for x in _row_find(cell, v, near, every=True, cy=(lo + hi) / 2, hh=hh)]
                    except Exception:
                        cands = []
                # 🔴 긴 값(품명)도 쪽 전체 OCR 에서 줄 하나가 통째로 빠진다(서류 A 의 Q'tz cover ring 행).
                #    그 행 띠만 크게 그려 다시 읽는다. 행마다 한 번만 읽고 돌려 쓴다.
                if not cands and ocr_cell and len(q) >= 3:
                    rk = (di, pno, round(lo), round(hi))
                    if rk not in row_ocr:
                        try:
                            row_ocr[rk] = ocr_cell(di, pno, (0, lo, 10000, hi))
                        except Exception:
                            row_ocr[rk] = []
                    cands = [x for x in _row_find(row_ocr[rk], v, near, every=True, cy=(lo + hi) / 2, hh=hh)
                             if c not in HEAD_ALIAS or (round(x[0]), round(x[1])) not in used[n]]
                # 🔴 그래도 못 읽은 수량 칸. 그 줄이 「수량 × 단가 = 금액」 검산을 통과했으면 수량 값은 확인된
                #    것이다. 그 줄과 수량 열이 만나는 칸을 짚는다. 검산을 못 통과한 줄은 짚지 않는다.
                if not cands and c in ("QUANTITY", "B/L quantity") and xs and ok_lines and ci_no.get(n) in ok_lines:
                    ws = sorted(colw.get(c) or [8.0])
                    x0 = xs[len(xs) // 2]; wd = ws[len(ws) // 2]
                    cands = [[x0, (lo + hi) / 2 - hh / 2, x0 + wd, (lo + hi) / 2 + hh / 2]]
                if not cands:
                    continue
                box = cands[0]
                # 🔴 한 자리 수는 줄 안에서도 여러 칸에 걸린다. 앞선 줄들이 잡아 둔 열 자리나 머리글
                #    자리에서 벗어나면 버린다. 수량 3 이 품명 칸을 짚어 「엉뚱한 데를 짚는다」 가 됐다.
                if len(q) == 1:
                    if near is None:
                        continue
                    lo_x, hi_x = (xs[0] - 8, xs[-1] + 8) if xs else (near - 30, near + 30)
                    if not (lo_x <= box[0] <= hi_x or lo_x <= box[2] <= hi_x):
                        continue
                if hl.get(key) != [[di, pno] + box]:
                    n_fill += 1
                hl[key] = [[di, pno] + box]
                colx[c].append(box[0]); colw[c].append(box[2] - box[0])
                if c in HEAD_ALIAS:
                    used[n].add((round(box[0]), round(box[1])))
    # 🔴 띠를 잡은 줄은 그 띠 밖을 짚지 않는다. 통째 찾기가 남긴 자리(포장명세서 줄이 인보이스
    #    견적 표를 짚은 것 같은)가 남아 있으면 점선이어야 할 자리에 실선 상자가 섞였다.
    for n, b in bands.items():
        for c in by_line[n]:
            key = f"L{n}:{c}"
            hs = hl.get(key) or []
            inb = [h for h in hs if (h[0], h[1]) == (b[0], b[1]) and b[2] <= (h[3] + h[5]) / 2 <= b[3]]
            if inb:
                hl[key] = inb[:1]
            else:
                # 띠 안에서 못 찾은 칸은 남은 자리를 두되, 다른 서류 쪽(포장명세서 줄이 인보이스 쪽을)
                # 짚은 자리는 뺀다
                keep_hs = [h for h in hs if page_kind(h[0], h[1]) == src[n]]
                if keep_hs:
                    hl[key] = keep_hs
                elif hs:
                    del hl[key]
    # 🔴 같은 품목이 한 쪽에 두 번 실린 표(견적가 표와 실제 청구 표)는 쓰지 않는 쪽도 점선으로 짚는다.
    #    쓰는 표만 칠하면 위 표가 통째로 희게 남아 「위 표는 못 읽었다」 로 보였다.
    if alt is not None:
        # 🔴 표 전체가 한 번 더 실린 경우만 받는다. 줄마다 쓰지 않은 띠가 같은 거리만큼 떨어져 있어야 한다.
        #    거리가 제각각이면 이웃 줄과 우연히 겹친 것이고, 그 자리는 이미 자기 줄로 칠해져 있다.
        # 쪽마다 따로 센다. 인보이스 줄과 포장명세서 줄은 다른 쪽에 띠가 있다
        per_page = collections.defaultdict(list)
        for n, b in bands.items():
            per_page[(b[0], b[1])].append(n)
        keep = {}
        for pg, ns in per_page.items():
            offs = collections.Counter()
            for n in ns:
                for o in {round((b[2] - bands[n][2]) / 3) for b in others.get(n, [])}:
                    offs[o] += 1
            if offs:
                o, cnt = offs.most_common(1)[0]
                if cnt >= 3 and cnt >= 0.6 * len(ns):
                    keep[pg] = o
        seen = set()
        for n, bs in others.items():
            o = keep.get((bands[n][0], bands[n][1]))
            if o is None:
                continue
            for b in bs:
                if round((b[2] - bands[n][2]) / 3) != o:
                    continue
                row = row_toks(b)
                for c, v in by_line[n].items():
                    if len(_norm(v)) < 2:
                        continue
                    box = _row_find(row, v)
                    if box:
                        r = (b[0], b[1], round(box[0]), round(box[1]))
                        if r not in seen:
                            seen.add(r); alt.append([b[0], b[1]] + box)
    return n_fill


def cols_of(by_line, n):
    return list(by_line.get(n, {}).items())


def _ocr_cell(path, pno, r):
    """쪽의 작은 칸 하나를 크게 그려 OCR 한다. 글 토막을 PDF 좌표로 돌려준다."""
    import pymupdf, tempfile
    pg = pymupdf.open(path)[pno - 1]
    clip = pymupdf.Rect(*r) & pg.rect
    if clip.is_empty or clip.width < 2 or clip.height < 2:
        return []
    z = 8
    fd, png = tempfile.mkstemp(suffix=".png"); os.close(fd)
    try:
        pg.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip).save(png)
        out = []
        for t, x, y, w, h in _ocr_lines(png):
            out.append((_norm(t), round(clip.x0 + x * clip.width, 1), round(clip.y0 + y * clip.height, 1),
                        round(clip.x0 + (x + w) * clip.width, 1), round(clip.y0 + (y + h) * clip.height, 1)))
        return out
    finally:
        os.remove(png)


def page_sizes(pdfs):
    import pymupdf
    out = []
    for p in pdfs:
        try: out.append([[round(pg.rect.width), round(pg.rect.height)] for pg in pymupdf.open(p)])
        except Exception: out.append([])
    return out


# 하이닉스 판에서 줄을 짚을 때 쓰는 열. 코드처럼 생긴 것과 금액만 찾는다.
# 🔴 수량은 안 찾는다. 한 자리 수가 쪽마다 걸려서 엉뚱한 자리를 짚는다(`search_variants` 의
#    awb_pieces 대목과 같은 이유다).
# 고객이 이름으로 부르는 칸을 먼저 짚는다. 시연에서 손이 먼저 가는 자리가 거기다.
HYNIX_HL_COLS = ["ITEM NAME", "P/O Number", "Material No", "SK HYNIX Sap Code", "SERIAL NUMBER",
                 "QUANTITY", "UNIT", "UNIT PRICE", "AMOUNT", "COUNTRY OF ORIGIN",
                 "Purchase Order", "Material", "Vendor Part No.", "Item", "B/L quantity", "Invoice amount"]

# 🔴 한두 자리 수는 쪽 아무 데나 걸린다. 통째 찾기에서는 빼고, 줄의 띠를 잡은 뒤 그 안에서만
#    찾는다. 수량과 단가를 아예 안 짚었더니 인보이스의 그 두 열만 내내 희게 남아, 보는 사람이
#    「거기는 못 읽었다」 로 읽었다.
BAND_ONLY = {"QUANTITY", "UNIT", "UNIT PRICE", "COUNTRY OF ORIGIN", "B/L quantity", "Net Price", "Item"}

# 띠를 잡는 닻. 줄마다 값이 다른 칸일수록 믿을 만하다
BAND_SPAN = 1.0          # 닻 높이의 몇 배까지 같은 줄로 보는가


def hynix_lines(ex, case, doc_text=""):
    """품목 줄과 줄마다 짚을 값. 짚는 일은 build_case 가 한 번에 한다.

    🔴 왜 줄까지 짚나. 하이닉스가 쓰는 값이 줄 안에 있다. 건 단위 칸만 짚어 주면 「우리가
       보는 숫자는 저기 없네」 가 된다. 서류에서 직접 읽었다는 것을 보여 주는 자리가
       이 판에서는 줄이다.

    🔴 왜 값 찾기를 여기서 안 하나. 스캔 쪽은 글자층이 없어서 pymupdf 검색이 못 찾고
       OCR 줄 좌표로 찾아야 한다. 그 OCR 은 케이스당 한 번만 돌리는 게 맞다. 그래서
       값만 모아 주고 짚는 것은 건 단위 칸과 같이 한 번에 한다. 따로 돌렸더니 전면
       스캔 케이스에서 짚을 자리가 0개였다.
    """
    import gerp_xlsx
    rows = gerp_xlsx.item_rows(case, ex)
    lex = {}
    for i, r in enumerate(rows):
        for col in HYNIX_HL_COLS:
            v = r.get(col)
            if v not in (None, ""):
                lex[f"L{i}:{col}"] = v
    extra = {"lines": rows, "inv": gerp_xlsx.invoice_rows(case, ex),
             "req_cov": gerp_xlsx.req_coverage(
                 gerp_xlsx.req_rows(case, ex, gerp_xlsx.field_available(doc_text or ""))),
             # 🔴 품명을 맨 앞에 둔다. GERP 여덟 열 뒤에 뒀더니 아홉째 칸이라 가로로 밀려
            #    화면에서 안 보였고, 「품명은 인식이 안 되네」 로 읽혔다. 실제로는 99.9% 뽑힌다.
            #    GERP 여덟 열은 순서를 그대로 이어 두어 그대로 복사해 붙일 수 있다.
            # 🔴 고객이 확정한 요구 항목 열 개를 맨 앞에 깐다(’26.09.30 임지원 PM 메일).
            #    이름도 메일에 적힌 그대로 둔다. 우리 이름으로 바꾸면 받는 쪽이 대조를 못 한다.
            #    GERP 여덟 열은 뒤에 순서 그대로 이어 두어 그대로 복사해 붙일 수 있다.
            "item_cols": [c for c, _ in gerp_xlsx.REQ_MAP] + gerp_xlsx.GERP_ITEM_COLS
                         + ["Case No.", "순중량(kg)", "총중량(kg)"],
             "gerp_only": gerp_xlsx.GERP_ONLY, "inv_cols": gerp_xlsx.INV_COLS,
             "hl_cols": HYNIX_HL_COLS}
    return extra, lex


def build_case(rec, other, root, docs_prefix="docs", hynix=False):
    # 🔴 판을 먼저 고른다. 안 고르면 기본 판(v3)의 스키마로 후처리가 돌고, 그 판에 없는 줄 칸
    #    (unit, sap_code, serial_no, material_code ...)이 글자가 아닌 것으로 취급돼 숫자 변환에
    #    날아간다. 화면에서 UNIT 이 44행 중 3행, SAP Code 가 19행으로 줄어든 채 나왔다.
    #    엑셀 쪽에서 같은 버그를 한 번 고쳤는데 화면 쪽에는 안 걸어 두었다.
    if hynix and docpipe.PROMPT_VERSION != "h1":
        docpipe.set_prompt("h1")
    ex = docpipe.postprocess(rec.get("raw") or {})
    for k in ("shipper_code", "consignee_code", "pol_code", "pod_code"):
        d = (rec.get("codes") or {}).get(k[:-5]) or {}
        if d.get("status") == "확정": ex[k] = d["code"]
    so = rec.get("so_row") or docpipe.to_so_row(ex)
    v = [list(x) for x in (rec.get("verify") or [])]
    case = rec.get("case", "")
    d = os.path.join(root, case) if root else ""
    pdfs = sorted(os.path.join(d, x) for x in os.listdir(d) if x.lower().endswith(".pdf")) if d and os.path.isdir(d) else []
    diff = differing(rec, other)
    mode = ex.get("transport_mode") or "AIR"
    issues = {}      # 칸 -> [설명]
    for g, k, m in v:
        if g == "OK": continue
        for f in RULE_FIELDS.get(k, [k]):
            issues.setdefault(f, []).append(rule_text(g, k, m, mode))
    v = [[g, k, m, rule_text(g, k, m, mode)] for g, k, m in v]
    level = {}
    for g, k, *_ in v:
        if g == "OK": continue
        for f in RULE_FIELDS.get(k, [k]):
            if level.get(f) != "FAIL": level[f] = g
    for k in diff:
        issues.setdefault(k, []).append("재추출 결과 불일치")
        level.setdefault(k, "WARN")
    keys = REQUIRED + OPTIONAL + ["shipper_address", "consignee_address"]
    _hy_text = ""
    if hynix and pdfs:
        # 🔴 엑셀 쪽과 같은 함수를 쓴다. 여기만 글자층을 직접 읽게 두었더니 스캔 서류에서
        #    PO 와 원산지 보완이 안 걸려 화면과 엑셀의 숫자가 달랐다.
        try:
            _hy_text = docpipe.doc_text_or_ocr(pdfs)
            docpipe.hynix_fill(ex, _hy_text, docpipe.doc_has_scan(pdfs))
        except Exception:
            _hy_text = ""
    extra, lex = hynix_lines(ex, case, _hy_text) if hynix else ({}, {})
    # 줄 값과 건 단위 칸을 같은 자리에서 짚는다. OCR 은 케이스당 한 번이면 된다.
    look = {**ex, **lex}
    keys_all = keys + list(lex)
    # 🔴 쪽 종류를 먼저 센다. 품목 행의 근거를 인보이스와 포장명세서 쪽에서 먼저 찾게 하려면
    #    찾기 전에 어느 쪽이 CI 이고 PL 인지 알아야 한다.
    kinds0 = page_kind_map(pdfs) if pdfs else []
    weak, ocr_lines = {}, {}
    hl = highlights(pdfs, look, keys_all, kinds0, weak)
    try:
        page_kinds = ocr_highlights(pdfs, look, keys_all, hl, ocr_lines) if pdfs else []
    except Exception:
        page_kinds = kinds0
    for wk, wv in weak.items():    # OCR 도 인보이스 쪽에서 못 찾은 자리는 찾아 둔 자리를 쓴다
        hl.setdefault(wk, wv)
    hl_alt = []
    if lex and pdfs:
        # 🔴 조용히 넘기지 않는다. 앞서 인자를 덜 넘겨 띠 짚기가 통째로 안 돌았는데, 화면은
        #    멀쩡히 나와서 하이라이트가 빈 것을 영상 찍고 나서야 알았다.
        try:
            kk = page_kinds or kinds0
            ok_lines = {int(k[7:]) for g, k, *_ in v if g == "OK" and re.match(r"^ci_line\d+$", str(k))}
            line_bands(lex, hl, page_tokens(pdfs, kk, ocr_lines), extra.get("lines"), kk, hl_alt,
                       lambda di, pno, r: _ocr_cell(pdfs[di], pno, r), ok_lines)
        except Exception as e:
            print(f"  [띠 짚기 건너뜀] {case}: {e}", file=sys.stderr)
        # 🔴 건 단위 칸(통화 USD, 원산지 KR 같은 짧은 값)은 품목 표 안에서 맨 처음 나온 자리를 짚는다.
        #    견적 표 첫 줄에 실선 상자가 생겨 「위 표도 읽어 썼다」 로 보였다. 품목 줄이 지나는 높이에
        #    떨어진 건 단위 상자는 뺀다.
        rows = {}
        for hk, hv in hl.items():
            if is_line_key(hk):
                for h in hv:
                    rows.setdefault((h[0], h[1]), []).append((h[3], h[5]))
        for h in hl_alt:
            rows.setdefault((h[0], h[1]), []).append((h[3], h[5]))
        for hk in [x for x in hl if not is_line_key(x)]:
            keep_h = [h for h in hl[hk] if not any(y0 - 1 <= (h[3] + h[5]) / 2 <= y1 + 1
                                                  for y0, y1 in rows.get((h[0], h[1]), []))]
            if keep_h:
                hl[hk] = keep_h
            else:
                del hl[hk]
    missing_req = [k for k in REQUIRED if ex.get(k) in (None, "", [])]
    missing_opt = [k for k in OPTIONAL if ex.get(k) in (None, "", [])]
    return {"case": case, "form": (rec.get("llm") or {}).get("forms") or "", "wall": rec.get("wall", 0),
            "ex": {k: ex.get(k) for k in docpipe.SCHEMA_V4["properties"]}, "so": so, "verify": v,
            "ok": sum(1 for g, *_ in v if g == "OK"), "warn": sum(1 for g, *_ in v if g == "WARN"),
            "fail": sum(1 for g, *_ in v if g == "FAIL"), "diff": diff, "issues": issues, "level": level,
            "mode": "SEA" if (rec.get("so_row") or {}).get("Service Group") == "SEA" or ex.get("transport_mode") == "SEA" else "AIR",
            "cost": ((rec.get("llm") or {}).get("api_usd")), "pages": rec.get("pages"),
            "missing_req": missing_req, "missing_opt": missing_opt,
            "pdfs": [f"{docs_prefix}/{case}/{os.path.basename(p)}" for p in pdfs],
            "why": next((rule_text(g, k, m, mode) for g, k, m in
                         [(x[0], x[1], x[2]) for x in v] if g == "FAIL"), ""),
            "sizes": page_sizes(pdfs), "hl": hl, "hl_alt": hl_alt, "page_kinds": page_kinds, **extra}


CSS = """
:root{--page:#f3f5f8;--surface:#fff;--sunk:#eef2f7;--line:#d9e0ea;--ink:#1f2937;--ink-2:#4b5563;--ink-3:#7b8794;
--blue:#1f4e9a;--blue-2:#2a9fd6;--blue-soft:#e6f1fb;--ok:#2e7d4f;--ok-soft:#dcefe3;--ask:#b26a00;--ask-soft:#fff1d6;--bad:#b3261e;--bad-soft:#fde2df;--hl:rgba(255,196,0,.12);--hl-on:rgba(255,140,0,.16)}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--page);color:var(--ink);font-family:"IBM Plex Sans KR","Noto Sans KR",system-ui,sans-serif;font-size:13.5px;line-height:1.5;word-break:keep-all}
.mono{font-family:ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}
header{height:52px;padding:0 18px;border-bottom:1px solid var(--line);background:var(--surface);display:flex;gap:18px;align-items:center}
header h1{font-size:16px;margin:0;color:var(--blue)}header .sub{color:var(--ink-3);font-size:12.5px}
.tabs{display:flex;gap:4px;margin-left:auto}.tabs button{border:1px solid var(--line);background:var(--surface);padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px;color:var(--ink-2)}
.tabs button.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.view{display:none;height:calc(100vh - 52px);grid-template-rows:minmax(0,1fr);overflow:hidden}
.view>*{min-height:0}.view.on{display:grid}
#v-confirm{grid-template-columns:300px 420px 1fr}#v-new{grid-template-columns:460px 1fr}
@media (max-width:1100px){#v-confirm{grid-template-columns:220px 1fr 1fr}}
.list{border-right:1px solid var(--line);background:var(--surface);overflow:auto}
.list input{width:calc(100% - 20px);margin:10px;padding:7px 9px;border:1px solid var(--line);border-radius:6px}
.filters{display:flex;gap:5px;padding:0 10px 8px;flex-wrap:wrap}.filters button{font-size:12px;padding:2px 8px;border-radius:12px;border:1px solid var(--line);background:var(--page);color:var(--ink-2);cursor:pointer}
.filters button.on{background:var(--blue-soft);border-color:var(--blue-2);color:var(--ink)}
.row{padding:8px 12px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:10px}
.row>div:first-child{flex:1;min-width:0}.row .meta{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row>.tag{flex:none;min-width:58px;justify-content:center}
.row:hover,.row.sel{background:var(--blue-soft)}.row .id{font-weight:600}.row .meta{color:var(--ink-3);font-size:11.5px}
.tag{display:inline-flex;align-items:center;height:20px;padding:0 8px;border-radius:10px;font-size:11px;font-weight:600;line-height:1;white-space:nowrap;box-sizing:border-box}
.tag.ok{background:var(--ok-soft);color:var(--ok)}.tag.ask{background:var(--ask-soft);color:var(--ask)}.tag.bad{background:var(--bad-soft);color:var(--bad)}
.detail{padding:12px 14px 40px;overflow:auto;border-right:1px solid var(--line);background:var(--surface)}
.card{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:12px;background:var(--surface)}
.card h2{font-size:13.5px;margin:0 0 8px;display:flex;justify-content:space-between;align-items:center}.card h2 span{font-weight:400;color:var(--ink-3);font-size:12px}
.field{display:grid;grid-template-columns:22px 1fr;gap:6px;padding:5px 4px;border-bottom:1px solid var(--line);cursor:pointer;border-radius:4px}
.field:hover{background:var(--sunk)}.field.on{background:var(--blue-soft)}.field b{display:block;color:var(--ink-3);font-weight:500;font-size:11.5px}
.field .v{font-size:13px}.field.req-miss{background:var(--bad-soft)}.field.issue .v{color:var(--ask)}.field .why{font-size:11.5px;color:var(--ask)}
.field.req-miss .why{color:var(--bad)}.empty{color:var(--ink-3)}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 12px;align-items:center}
.btn{background:var(--blue-2);color:#fff;border:0;padding:7px 14px;border-radius:5px;font-weight:600;cursor:pointer;text-decoration:none;font-size:13px;display:inline-block}
.btn.ghost{background:var(--surface);color:var(--ink);border:1px solid var(--line);font-weight:500}
.viewer{display:flex;flex-direction:column;min-width:0;min-height:0}.vbar{display:flex;gap:6px;align-items:center;padding:6px 10px;border-bottom:1px solid var(--line);background:var(--surface);font-size:12.5px}
.vbar button{border:1px solid var(--line);background:var(--surface);padding:3px 9px;border-radius:4px;cursor:pointer;white-space:nowrap;min-width:44px}
.pages{overflow:auto;flex:1;min-height:0;padding:12px;background:#dfe4ea;position:relative}.pg{position:relative;margin:0 auto 12px;box-shadow:0 1px 4px rgba(0,0,0,.25);background:#fff}
.pg canvas{display:block}.hl{position:absolute;background:var(--hl);border:1.5px solid rgba(214,140,0,.78);border-radius:2px;pointer-events:none}.hl.alt{background:none;border:1.25px dashed rgba(71,85,105,.6)}
.hn.bad{color:#c0564b}.hn.ask{color:#c28a1e}.hn.ok{color:#3f8a62}
.tag.rule{background:#e8edf3;color:#5a6b7d}.chk-row.pr b,.chk-row.pr .mono{color:var(--ink-3)}
.rulef{margin-left:6px;font-size:11px;border:1px solid var(--blue-2);background:var(--blue-soft);color:var(--blue);border-radius:10px;padding:1px 8px;cursor:pointer}
.chk{border-color:#f0c9c4}.chk-row{display:grid;grid-template-columns:auto auto 1fr;gap:8px;align-items:baseline;padding:5px 4px;border-top:1px solid var(--line);cursor:pointer;font-size:12.5px;border-radius:4px}
.chk-row:hover{background:var(--sunk)}.chk-row .mono{color:var(--ink-2);font-size:12px;overflow-wrap:anywhere}
.chk-ok{margin:0 0 10px;padding:7px 12px;border-radius:6px;background:var(--ok-soft);color:var(--ok);font-size:12.5px;font-weight:600}
.pg.flash{outline:4px solid #e67e00;outline-offset:2px;transition:outline-color 1.5s}
.hl.on{background:var(--hl-on);border:2px solid #e2650a;box-shadow:0 0 0 3px rgba(255,120,0,.22)}.hl.warn{background:rgba(255,140,0,.12);border:1.5px solid #e67e00}.hl.err{background:rgba(220,38,38,.10);border:1.5px solid #c81e1e}.hl.err.on,.hl.warn.on{box-shadow:0 0 0 3px rgba(200,30,30,.35)}
/* 🔴 두 판을 한 화면 코드로 쓰므로, 지금 보는 것이 어느 판인지 눈에 바로 들어와야 한다.
   데모에서 둘을 나란히 열어 두면 표만 보고는 구분이 안 된다. 머리글 색까지 갈라 둔다. */
.ed{display:inline-flex;align-items:center;height:22px;padding:0 10px;border-radius:11px;font-size:11.5px;font-weight:600;
    background:var(--sunk);color:var(--ink-2);white-space:nowrap}
.ed.hy{background:#ede4fb;color:#5b3ea8}
.swap{font-size:12px;color:var(--ink-3);text-decoration:none;border:1px solid var(--line);border-radius:6px;padding:3px 9px;white-space:nowrap}
.swap:hover{border-color:var(--blue-2);color:var(--blue)}
body.hynix header,body.hyedition header{box-shadow:inset 0 3px 0 #7c5cc4}
body.hyedition header h1{color:#5b3ea8}
.why2{color:var(--bad);font-size:11px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cov{border-collapse:collapse;width:100%;font-size:11.5px;margin-bottom:10px}
.cov th,.cov td{border:1px solid var(--line);padding:3px 7px;text-align:left;white-space:nowrap}
.cov th{background:var(--sunk);font-weight:600;color:var(--ink-2)}
.cov td.mono{text-align:right}.cov td.b{font-weight:600}
.cov tr.full td.b{color:var(--ok)}
.lt tr.unver td:first-child::before{content:none}
.unvmark{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--ask);vertical-align:middle}
.lnote{margin:0 0 8px;font-size:11.5px;color:var(--ink-3);line-height:1.5}
.lwrap{overflow:auto;max-height:340px;border:1px solid var(--line);border-radius:6px}
.lt{border-collapse:collapse;font-size:11.5px;white-space:nowrap}
.lt th,.lt td{border:1px solid var(--line);padding:2px 6px;text-align:left}
.lt th{background:var(--sunk);position:sticky;top:0;z-index:2;font-weight:600;color:var(--ink-2)}
.lt td.go{background:var(--sunk);color:var(--ink-3)}
.lt td.hit{cursor:pointer}.lt td.hit:hover{background:var(--blue-soft)}
.lt td[contenteditable]:focus{outline:2px solid var(--blue-2);outline-offset:-2px;background:var(--blue-soft)}
.lt tr.gap td{background:var(--ask-soft)}
.lt tr.edited td:first-child{box-shadow:inset 3px 0 0 var(--blue-2)}
body.hynix #v-confirm{grid-template-columns:260px 560px 1fr}
body.hynix #v-new{grid-template-columns:560px 1fr}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:4px 12px}.grid div{padding:3px 0;border-bottom:1px solid var(--line);font-size:12.5px}
.grid b{display:block;color:var(--ink-3);font-weight:500;font-size:11px}
pre{background:var(--sunk);padding:10px;border-radius:6px;overflow:auto;font-size:12px}
.up{background:var(--page)}.up .card{padding:14px 16px;border-radius:10px}
.dz{display:flex;flex-direction:column;align-items:center;gap:4px;border:1.5px dashed #b8c4d3;border-radius:10px;padding:26px 16px;text-align:center;background:var(--sunk);cursor:pointer;transition:all .15s}
.dz:hover,.dz.over{border-color:var(--blue-2);background:var(--blue-soft)}.dz input{display:none}.dz b{font-size:13.5px;color:var(--ink)}.dz span{font-size:12px;color:var(--ink-3)}
.flist{list-style:none;margin:10px 0 0;padding:0}.flist li{display:grid;grid-template-columns:1fr auto 24px;gap:8px;align-items:center;padding:6px 8px;border:1px solid var(--line);border-radius:7px;margin-top:5px;font-size:12.5px;background:var(--surface)}
.flist .fn{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.flist .fs{color:var(--ink-3);font-size:11.5px}.flist button{border:0;background:none;color:var(--ink-3);font-size:16px;cursor:pointer;line-height:1}.flist button:hover{color:var(--bad)}
.uact{display:flex;gap:8px;margin-top:12px}.uact .btn{padding:8px 16px}.btn:disabled{opacity:.45;cursor:default}
.pill{font-size:11px!important;font-weight:600!important;border-radius:10px;padding:2px 9px;color:var(--ink-2)!important;background:var(--sunk)}.pill.run{background:var(--blue-soft);color:var(--blue)!important}.pill.ok{background:var(--ok-soft);color:var(--ok)!important}.pill.bad{background:var(--bad-soft);color:var(--bad)!important}
.clockrow{display:flex;align-items:baseline;gap:12px;margin:2px 0 8px}.clk-sub{font-size:12px;color:var(--ink-3)}
.tl{list-style:none;margin:6px 0 0;padding:0 0 0 4px;position:relative}
.tl li{position:relative;display:grid;grid-template-columns:1fr auto;gap:10px;padding:6px 0 6px 22px;font-size:12.5px;color:var(--ink-2)}
.tl li::before{content:"";position:absolute;left:5px;top:0;bottom:0;border-left:2px solid var(--line)}.tl li:first-child::before{top:12px}.tl li:last-child::before{bottom:calc(100% - 12px)}
.tl li i{position:absolute;left:0;top:8px;width:12px;height:12px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 3px var(--surface)}
.tl li.run i{background:var(--blue-2);animation:pulse 1s infinite}.tl li.run{color:var(--blue)}.tl li.err i{background:var(--bad)}.tl li.err{color:var(--bad)}
.tl .sec{color:var(--ink-3);font-size:12px}
@keyframes pulse{50%{box-shadow:0 0 0 6px var(--blue-soft)}}
.sumg{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px}.sumg:empty{display:none}
.sumg div{background:var(--sunk);border-radius:8px;padding:8px 10px}.sumg span{display:block;font-size:11px;color:var(--ink-3)}.sumg b{font-size:15px}
.sumg em{font-style:normal;font-size:12.5px}.sumg em.e{color:var(--bad)}.sumg em.w{color:var(--ask)}
.devlog summary{cursor:pointer;font-weight:600;font-size:13.5px}.devlog summary span{font-weight:400;color:var(--ink-3);font-size:12px;margin-left:6px}.devlog pre{margin:10px 0 0;max-height:240px}
.vempty{height:100%;min-height:300px;display:flex;align-items:center;justify-content:center;color:var(--ink-3);font-size:13px}
.steps{list-style:none;padding:0;margin:8px 0}.steps li{padding:5px 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;font-size:12.5px}
.steps li.done{color:var(--ink-2)}.steps li.run{color:var(--blue)}.clock{font-size:26px;font-weight:600;color:var(--blue)}
"""

APP_JS = r"""
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
const RULE_NAMES = __RULE_NAMES__;
const DATA = __DATA__, SO_COLS = __SO_COLS__, REQUIRED = __REQUIRED__, OPTIONAL = __OPTIONAL__, LABELS = __LABELS__;
let filter='all', sel=null, scale=1.0, pdfDocs=[], curHl=null, curCase=null;
const q=document.getElementById('q'), list=document.getElementById('rows'), det=document.getElementById('detail');
let PROMO=new Set(); try{ PROMO=new Set(JSON.parse(localStorage.getItem('docx_promo')||'[]')); }catch(e){}
function promoted(c,v){ return v[0]!=='OK' && PROMO.has(v[1]) && (c.promo||[]).includes(v[1]); }
function eff(c){ let f=0,w=0; for(const v of c.verify){ if(v[0]==='OK'||promoted(c,v)) continue; if(v[0]==='FAIL') f++; else w++; } return [f,w]; }
function missEff(c){ return c.missing_req.filter(k=>!(PROMO.has('shipper_history')&&c.fill&&c.fill[k])); }
function status(c){ const [f,w]=eff(c); const ms=missEff(c); if(f>0||ms.length) return ['bad', f>0?'오류 '+f:'필수값 누락']; if(c.diff.length||w>0) return ['ask','확인']; return ['ok','정상']; }
function counts(){ const n={bad:0,ask:0,ok:0}; DATA.forEach(c=>n[status(c)[0]]++); return n; }
function refreshCounts(){ const n=counts(), t=DATA.length;
  for(const k of ['bad','ask','ok']){ const h=document.getElementById('h-'+k); if(h) h.textContent=n[k]; const d=document.getElementById('d-'+k); if(d) d.innerHTML=n[k]+'<small>'+Math.round(n[k]/t*100)+'%</small>'; }
  if(window.promoPaint) promoPaint(n); }
function ruleHit(c,r){ if(r==='_missing') return c.missing_req.length>0; return c.verify.some(v=>v[0]!=='OK'&&(v[1]===r||(r==='ci_line'&&v[1].startsWith('ci_line')))); }
function passes(c){ if(filter==='all') return true; if(filter.startsWith('rule:')) return ruleHit(c, filter.slice(5)); return status(c)[0]===filter||c.mode===filter; }
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function val(v){ if(v==null||v==='') return '<span class="empty">-</span>'; if(Array.isArray(v)) return '<span class="mono">'+esc(JSON.stringify(v)).slice(0,160)+'</span>'; return esc(v); }
function render(){
  const term=q.value.trim().toUpperCase(); list.innerHTML='';
  DATA.filter(c=>(!term||c.case.toUpperCase().includes(term)||(c.ex.shipper_name||'').toUpperCase().includes(term))&&passes(c))
      .forEach(c=>{const [cls,txt]=status(c); const d=document.createElement('div'); d.className='row'+(sel===c?' sel':'');
        const w2=(cls==='bad'&&c.why)?`<div class="why2">${esc(c.why)}</div>`:'';
        d.innerHTML=`<div><div class="id mono">${esc(c.case)}</div><div class="meta">${esc(c.mode)} | ${esc(c.wall)}초 | ${esc((c.ex.shipper_name||'').slice(0,20))}</div>${w2}</div><span class="tag ${cls}">${txt}</span>`;
        d.onclick=()=>{sel=c;render();show(c,det,'pages');}; list.appendChild(d);});
  const cnt=document.getElementById('count'); cnt.textContent=list.children.length+'건';
  if(filter.startsWith('rule:')){ const b=document.createElement('button'); b.className='rulef'; b.textContent=(filter==='rule:_missing'?'필수값 누락':(RULE_NAMES[filter.slice(5)]||filter.slice(5)))+' 해제'; b.onclick=()=>{filter='all'; document.querySelectorAll('.filters button').forEach(x=>x.classList.toggle('on',x.dataset.f==='all')); render();}; cnt.appendChild(b); }
}
function fieldRow(c,k,required){
  const v=c.ex[k], miss=(v==null||v===''||(Array.isArray(v)&&!v.length)), why=(c.issues[k]||[]).join(', ');
  const cls=['field', miss&&required?'req-miss':'', why?'issue':''].join(' ');
  const checked=!miss&&!why;
  return `<div class="${cls}" data-k="${k}" onclick="jump('${k}')"><input type="checkbox" ${checked?'checked':''} onclick="event.stopPropagation()" title="확인"><div><b>${LABELS[k]||k}${required?' <span class="tag bad" style="font-size:10px">필수</span>':''}</b><div class="v">${val(v)}</div>${why?`<div class="why">${esc(why)}</div>`:miss&&required?'<div class="why">필수값 누락</div>':miss?'':''}</div></div>`;
}
function show(c, target, pagesId){
  curCase=c; const [cls,txt]=status(c);
  document.body.classList.toggle('hynix', !!c.lines);   // 줄 표가 붙는 판은 가운데 칸이 넓어야 한다
  let h=`<div class="actions"><a class="btn" href="kerol_mock.html" onclick="send()">KEROL 등록</a>${c.lines?'<button class="btn ghost" onclick="xlsx()">엑셀 내려받기 (전체 '+DATA.filter(x=>x.lines).length+'건)</button>':''}<span class="tag ${cls}">${txt}</span><span class="sub mono" style="color:var(--ink-3)">${c.mode} | ${c.pages||''}쪽 | ${c.wall}초${c.cost!=null?' | $'+c.cost.toFixed(3)+' (약 '+Math.round(c.cost*1400)+'원)':''}</span></div>`;
  const nIss=Object.keys(c.issues).length;
  const iss=c.verify.filter(v=>v[0]!=='OK').sort((a,b)=>(promoted(c,a)?2:a[0]==='FAIL'?0:1)-(promoted(c,b)?2:b[0]==='FAIL'?0:1)); const [ef,ew]=eff(c);
  const filled=PROMO.has('shipper_history')&&c.fill?Object.keys(c.fill).filter(k=>c.missing_req.includes(k)):[];
  filled.forEach(k=>iss.push(['WARN','__fill_'+k,'',`${LABELS[k]||k}: ${c.fill[k]} (같은 송하인 KEROL 등록 이력)`]));
  if(iss.length){ h+=`<div class="card chk"><h2>검증 <span>오류 ${ef} | 확인 ${ew} | 정상 ${c.ok}</span></h2>`;
    iss.forEach(v=>{const t=(v[3]||v[2]).split(': '); const pr=promoted(c,v)||v[1].startsWith('__fill_'); h+=`<div class="chk-row${pr?' pr':''}" onclick="jumpRule('${v[1]}')"><span class="tag ${pr?'rule':v[0]==='FAIL'?'bad':'ask'}">${pr?'규칙 적용':v[0]==='FAIL'?'오류':'확인'}</span><b>${esc(t[0])}</b><span class="mono">${esc(t.slice(1).join(': '))}${pr&&!v[1].startsWith('__fill_')?' (KEROL에는 운송장 값 등록)':''}</span></div>`;});
    h+='</div>'; }
  else h+=`<div class="chk-ok">검증 ${c.ok}건 이상 없음</div>`;
  h+=`<div class="card"><h2>필수값 <span>${REQUIRED.length-c.missing_req.length}/${REQUIRED.length}</span></h2>${REQUIRED.map(k=>fieldRow(c,k,true)).join('')}</div>`;
  h+=`<div class="card"><h2>선택값 <span>${c.missing_opt.length?'미입력 '+c.missing_opt.length:''}</span></h2>${OPTIONAL.map(k=>fieldRow(c,k,false)).join('')}</div>`;
  if(c.lines) h+=lineCard(c);
  h+=`<div class="card"><h2>품목과 주소</h2>${['ci_lines','pl_lines','shipper_address','consignee_address'].map(k=>fieldRow(c,k,false)).join('')}</div>`;
  h+=`<div class="card"><h2>KEROL 등록 항목 <span>${Object.values(c.so).filter(x=>x!==''&&x!=null).length}/60</span></h2><div class="grid">`;
  SO_COLS.forEach(col=>{h+=`<div><b>${col}</b>${val(c.so[col])}</div>`;}); h+='</div></div>';
  target.innerHTML=h; if(c.lines) bindLines(); loadPdfs(c, pagesId);
}
function lineCard(c){
  const cols=c.item_cols, only=new Set(c.gerp_only||[]), hlc=new Set(c.hl_cols||[]);
  const gap=r=>!String(r['Purchase Order']||'').trim()||(!String(r['Material']||'').trim()&&!String(r['Vendor Part No.']||'').trim());
  const nGap=c.lines.filter(gap).length;
  let t=`<div class="card"><h2>운영 데이터 (GERP B/L Item Update) <span>${c.lines.length}행${nGap?' | 확인 필요 '+nGap:''}</span></h2>`;
  if(c.req_cov){
    t+='<table class="cov"><thead><tr><th>요구 항목</th><th>추출</th><th>전체 기준</th><th>열 있는 행</th><th>열 기준</th></tr></thead><tbody>';
    c.req_cov.forEach(r=>{ const full=r['열 기준']==='100.0%';
      t+=`<tr class="${full?'full':''}"><td>${esc(r['항목'])}</td><td class="mono">${r['추출 행']}/${r['전체 행']}</td>`
        +`<td class="mono">${esc(r['전체 기준'])}</td><td class="mono">${r['열이 있는 행']}</td>`
        +`<td class="mono b">${esc(r['열 기준'])}</td></tr>`;});
    t+='</tbody></table><p class="lnote">「열 기준」 은 해당 열이 인쇄된 서류의 행만 센 값입니다. 서류에 그 열이 없어 빈 것과 읽지 못한 것을 가릅니다.</p>';
  }
  t+='<p class="lnote">Commercial Invoice 와 Packing List 에서 판독한 행 단위 값입니다. 항목 선택 시 우측 원본의 해당 위치를 표시합니다. 수정 값은 엑셀에 반영됩니다.</p>';
  t+='<div class="lwrap"><table class="lt"><thead><tr>'+cols.map(x=>`<th>${esc(x)}</th>`).join('')+'</tr></thead><tbody>';
  c.lines.forEach((r,i)=>{
    const cl=[gap(r)?'gap':'', r._unver?'unver':''].filter(Boolean).join(' ');
    t+=`<tr${cl?' class="'+cl+'"':''}${r._src?' title="'+esc(r._src)+'"':''}>`+cols.map(x=>{
      const k=`L${i}:${x}`, has=hlc.has(x)&&c.hl[k];
      return `<td class="${only.has(x)?'go':''}${has?' hit':''}" data-hl="${has?k:''}" data-i="${i}" data-c="${esc(x)}"
        contenteditable="true">${esc(r[x])}</td>`;
    }).join('')+'</tr>';
  });
  t+='</tbody></table></div>';
  const nSrc=c.lines.filter(r=>r._src).length, nUn=c.lines.filter(r=>r._unver).length;
  if(nSrc||nUn) t+=`<p class="lnote">${nSrc?'보완 '+nSrc+'행 (통관 용도설명서 또는 서류 머리에서 확보. 행에 마우스를 올리면 출처 표시). ':''}`
    +`${nUn?nUn+'행의 품목코드는 통관 용도설명서 그림에서 읽은 값입니다.':''}</p>`;
  if(c.inv&&c.inv.length>1){
    t+='<h2 style="margin-top:10px">인보이스별 합계 <span>'+c.inv.length+'장</span></h2>';
    t+='<div class="lwrap"><table class="lt"><thead><tr>'+c.inv_cols.filter(x=>x!=='케이스').map(x=>`<th>${esc(x)}</th>`).join('')+'</tr></thead><tbody>';
    c.inv.forEach(r=>{ t+='<tr>'+c.inv_cols.filter(x=>x!=='케이스').map(x=>`<td>${esc(r[x])}</td>`).join('')+'</tr>'; });
    t+='</tbody></table></div>';
  }
  return t+'</div>';
}
function bindLines(){
  document.querySelectorAll('.lt td[contenteditable]').forEach(td=>{
    td.onclick=()=>{ if(td.dataset.hl) jump(td.dataset.hl); };
    td.oninput=()=>{ curCase.lines[+td.dataset.i][td.dataset.c]=td.textContent.trim(); td.parentNode.classList.add('edited'); };
  });
}
async function xlsx(){
  if(!curCase) return;
  // 🔴 화면에 있는 건을 전부 담는다. 한 건만 받으면 받는 쪽이 건마다 다시 눌러야 한다.
  const cases=DATA.filter(c=>c.lines).map(c=>({case:c.case, lines:c.lines, inv:c.inv, ex:c.ex, verify:c.verify}));
  const r=await fetch('/api/xlsx',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({cases:cases})});
  if(!r.ok){ alert('엑셀 생성에 실패했습니다.'); return; }
  const b=await r.blob(), a=document.createElement('a');
  a.href=URL.createObjectURL(b); a.download='하이닉스우시_요구항목_'+cases.length+'건.xlsx'; a.click();
}
const RULE_FIELDS=__RULE_FIELDS__;
const KIND_OF=k=>/^(pl_|package_unit|dims_cm|measurement)/.test(k)?'PL':/^(ci_|currency|incoterms|invoice_no)/.test(k)?'CI':'AWB';
function jumpRule(rule){ const ks=RULE_FIELDS[rule]||[rule]; for(const k of ks){ if(curCase.hl[k]){jump(k);return;} } jump(ks[0]); }
async function jumpPage(kind){ const pk=curCase.page_kinds||[]; let at=null;
  for(let di=0; di<pk.length && !at; di++) for(let pi=0; pi<pk[di].length; pi++){ if(pk[di][pi]===kind){ at=[di,pi+1]; break; } }
  if(!at) for(let di=0; di<pk.length && !at; di++) for(let pi=0; pi<pk[di].length; pi++){ if(pk[di][pi]==='SCAN'){ at=[di,pi+1]; break; } }
  if(!at) return false; if(scale<1.3){ scale=1.3; document.querySelectorAll('.zl').forEach(e=>e.textContent='130%'); await drawAll(); } const wrap=pgEl(at[0], at[1]); if(!wrap) return false;
  wrap.parentElement.scrollTo({top:wrap.offsetTop-10, behavior:'smooth'}); wrap.classList.add('flash'); setTimeout(()=>wrap.classList.remove('flash'),1600); return true; }
async function loadPdfs(c, pagesId){
  const cont=document.getElementById(pagesId); cont.innerHTML='<div class="empty" style="padding:20px">불러오는 중</div>'; pdfDocs=[];
  for(const p of c.pdfs){ try{ pdfDocs.push(await pdfjsLib.getDocument(p).promise); }catch(e){ pdfDocs.push(null);} }
  scale=fitScale(); document.querySelectorAll('.zl').forEach(e=>e.textContent=Math.round(scale*100)+'%');
  await drawAll(cont);
  if(c.lines) landLine();   // 품목 판은 운송장이 아니라 인보이스 쪽을 먼저 띄운다
}
// 🔴 원본 칸은 늘 1쪽인 운송장에서 열렸다. 이 판에서 보는 값은 품목 행이고 그 근거는
//    Commercial Invoice 와 Packing List 다. 열자마자 그 쪽을 띄운다.
function landLine(){
  const pk=(curCase&&curCase.page_kinds)||[]; let at=null;
  for(const want of ['CI','PL']){
    for(let di=0; di<pk.length && !at; di++)
      for(let pi=0; pi<pk[di].length; pi++) if(pk[di][pi]===want){ at=[di,pi+1]; break; }
    if(at) break;
  }
  if(!at) return false;
  // 🔴 쪽 이름(pg-0-3)은 검토 화면과 서류 등록 화면에 똑같이 있다. getElementById 는 숨은 검토 화면 쪽을
  //    돌려줘서, 서류 등록 결과는 내내 1쪽 운송장에 머물렀다. 지금 보이는 원본 칸 안에서 찾는다.
  const wrap=pgEl(at[0], at[1]); if(!wrap) return false;
  wrap.parentElement.scrollTo({top:Math.max(0, wrap.offsetTop-10)}); return true;
}
async function drawAll(cont){
  cont=cont||document.getElementById(currentPagesId()); cont.innerHTML='';
  for(let di=0; di<pdfDocs.length; di++){ const doc=pdfDocs[di]; if(!doc) continue;
    for(let pi=1; pi<=doc.numPages; pi++){ const page=await doc.getPage(pi); const vp=page.getViewport({scale}); const wrap=document.createElement('div'); wrap.className='pg'; wrap.id=`pg-${di}-${pi}`; wrap.style.width=vp.width+'px'; wrap.style.height=vp.height+'px';
      const cv=document.createElement('canvas'); cv.width=vp.width; cv.height=vp.height; wrap.appendChild(cv); cont.appendChild(wrap);
      await page.render({canvasContext:cv.getContext('2d'), viewport:vp}).promise;
      const base=page.getViewport({scale:1});
      // 🔴 같은 자리에 상자를 겹쳐 그리지 않는다. 인보이스 줄과 포장명세서 줄이 같은 칸을 짚으면
      //    반투명 상자가 두세 겹 쌓여 진한 노랑이 되고, 상자 안 글자가 안 보였다.
      const f=vp.width/base.width, seen=new Map();
      const box=(h,cls)=>{ const d=document.createElement('div'); d.className=cls; d._ks=new Set(); d.dataset.r=h.slice(0,4).join(',');
        d.style.left=(h[2]*f-2)+'px'; d.style.top=(h[3]*f-2)+'px'; d.style.width=((h[4]-h[2])*f+4)+'px'; d.style.height=((h[5]-h[3])*f+4)+'px'; wrap.appendChild(d); return d; };
      for(const h of (curCase.hl_alt||[])){ if(h[0]===di&&h[1]===pi) box(h,'hl alt'); }
      for(const [k,hits] of Object.entries(curCase.hl)){ for(const h of hits){ if(h[0]!==di||h[1]!==pi) continue;
        const r=h.slice(0,4).join(','); let d=seen.get(r);
        if(!d){ const lv=(curCase.level||{})[k]; d=box(h,'hl'+(lv==='FAIL'?' err':lv?' warn':'')); seen.set(r,d); d.title=(LABELS[k]||k); }
        d._ks.add(k); if(curHl===k) d.classList.add('on'); } } } }
}
const hasK=(d,k)=>!!(d._ks&&d._ks.has(k));
function currentPagesId(){ return document.getElementById('v-new').classList.contains('on')?'pages2':'pages'; }
function pgEl(di, pi){ const c=document.getElementById(currentPagesId()); return (c&&c.querySelector(`#pg-${di}-${pi}`))||document.getElementById(`pg-${di}-${pi}`); }
async function jump(k){ curHl=k; document.querySelectorAll('.field').forEach(f=>f.classList.toggle('on',f.dataset.k===k)); const hits=curCase.hl[k]; document.querySelectorAll('.hl').forEach(d=>d.classList.toggle('on',hasK(d,k)));
  if(!hits||!hits.length){ await jumpPage(KIND_OF(k)); return; } if(scale<1.8){ scale=1.8; document.querySelectorAll('.zl').forEach(e=>e.textContent='180%'); await drawAll(); } const h=hits[0]; const wrap=pgEl(h[0], h[1]); if(!wrap) return; const cont=wrap.parentElement;
  // 🔴 같은 값이 여러 쪽에 찍힌다. 화면이 실제로 가는 자리는 첫 자리 하나뿐이므로 그것만 따로
  //    표시한다. 표시가 없으면 「첫 .hl.on」 이 쪽 차례상 앞인 운송장 쪽이 되어, 녹화 도구가
  //    인보이스로 간 화면을 두고 운송장 자리를 짚었다.
  document.querySelectorAll('.hl.lead').forEach(d=>d.classList.remove('lead'));
  const lead=wrap.querySelector(`.hl[data-r="${h.slice(0,4).join(',')}"]`); if(lead) lead.classList.add('lead');
  const f=wrap.offsetWidth/curCase.sizes[h[0]][h[1]-1][0]; cont.scrollTo({top: wrap.offsetTop + h[3]*f - cont.clientHeight/2 + 20, left: Math.max(0, h[2]*f - cont.clientWidth/2), behavior:'smooth'}); }
async function zoom(d){ scale=Math.min(4, Math.max(0.4, d===0?fitScale():scale*(d>0?1.25:0.8))); document.querySelectorAll('.zl').forEach(e=>e.textContent=Math.round(scale*100)+'%'); await drawAll(); }
function fitScale(){ const cont=document.getElementById(currentPagesId()); const w=curCase&&curCase.sizes[0]&&curCase.sizes[0][0]?curCase.sizes[0][0][0]:595; return (cont.clientWidth-40)/w; }
function send(){ if(!curCase) return; localStorage.setItem('kerol_so_inbox', JSON.stringify({case:curCase.case, so:curCase.so, ex:curCase.ex, at:new Date().toISOString()})); }
q.oninput=render; document.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{filter=b.dataset.f;document.querySelectorAll('.filters button').forEach(x=>x.classList.toggle('on',x===b));render();});
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('on',x===b));document.querySelectorAll('.view').forEach(v=>v.classList.toggle('on',v.id===b.dataset.v)); if(b.dataset.v==='v-cap'&&window.capLines) setTimeout(capLines,30);});
render(); if(DATA.length){sel=DATA[0];render();scale=1.0;show(sel,det,'pages');}
// 서류 올리기
const fileIn=document.getElementById('files'), stepsEl=document.getElementById('steps'), clock=document.getElementById('clock'), dz=document.getElementById('dz'), flist=document.getElementById('flist'), goBtn=document.getElementById('go');
let t0=null, timer=null, job=null, picked=[];
function kb(n){ return n>1048576?(n/1048576).toFixed(1)+' MB':Math.max(1,Math.round(n/1024))+' KB'; }
function drawFiles(){ flist.innerHTML=picked.map((f,i)=>`<li><span class="fn">${esc(f.name)}</span><span class="fs mono">${kb(f.size)}</span><button onclick="picked.splice(${i},1);drawFiles()" title="빼기">×</button></li>`).join('');
  goBtn.disabled=!picked.length; document.getElementById('clksub').textContent=picked.length?`${picked.length}개 파일, 합계 ${kb(picked.reduce((a,f)=>a+f.size,0))}`:'파일을 올리면 시작할 수 있습니다'; }
function addFiles(fs){ for(const f of fs){ if(/\.pdf$/i.test(f.name)&&!picked.some(x=>x.name===f.name&&x.size===f.size)) picked.push(f); } drawFiles(); }
fileIn.onchange=()=>{ addFiles(fileIn.files); fileIn.value=''; };
['dragenter','dragover'].forEach(e=>dz.addEventListener(e,ev=>{ev.preventDefault(); dz.classList.add('over');}));
['dragleave','drop'].forEach(e=>dz.addEventListener(e,ev=>{ev.preventDefault(); dz.classList.remove('over');}));
dz.addEventListener('drop',ev=>addFiles(ev.dataTransfer.files));
document.getElementById('clr').onclick=()=>{ picked=[]; drawFiles(); };
function setStat(cls,txt){ const e=document.getElementById('jstat'); e.className='pill '+cls; e.textContent=txt; }
goBtn.onclick=async()=>{ if(!picked.length) return; const fd=new FormData(); for(const f of picked) fd.append('pdf', f, f.name);
  goBtn.disabled=true; stepsEl.innerHTML=''; document.getElementById('sumg').innerHTML=''; document.getElementById('result2').innerHTML='';
  document.getElementById('pages2').innerHTML='<div class="vempty">추출이 끝나면 원본 PDF가 여기에 표시됩니다</div>'; setStat('run','진행 중');
  t0=Date.now(); clock.textContent='0.0초'; timer=setInterval(()=>{clock.textContent=((Date.now()-t0)/1000).toFixed(1)+'초';},100);
  const r=await fetch('/api/extract',{method:'POST',body:fd}); job=(await r.json()).job; poll(); };
async function poll(){ const r=await fetch('/api/job/'+job); const j=await r.json();
  stepsEl.innerHTML=j.steps.map(s=>`<li class="${s.done?'done':'run'}"><i></i><span class="tx">${esc(s.text)}</span><span class="sec mono">${s.sec!=null&&s.sec>=0.05?s.sec.toFixed(1)+'초':''}</span></li>`).join('');
  if(j.status==='done'){ clearInterval(timer); clock.textContent=j.wall.toFixed(1)+'초'; setStat('ok','완료'); goBtn.disabled=!picked.length; const c=j.case;
    const calls=j.steps.filter(s=>/추출/.test(s.text)).length;
    document.getElementById('sumg').innerHTML=`<div><span>소요 시간</span><b class="mono">${j.wall.toFixed(1)}초</b></div><div><span>모델 호출</span><b class="mono">${calls}회</b></div>`+
      `<div><span>API 환산 비용</span><b class="mono">${c.cost!=null?'$'+c.cost.toFixed(3):'-'}</b></div><div><span>검증</span><b><em class="e">오류 ${c.fail}</em> <em class="w">확인 ${c.warn}</em></b></div>`;
    curCase=c; show(c, document.getElementById('result2'), 'pages2'); }
  else if(j.status==='error'){ clearInterval(timer); setStat('bad','오류'); goBtn.disabled=!picked.length; stepsEl.innerHTML+=`<li class="err"><i></i><span class="tx">${esc(j.error)}</span></li>`; }
  else setTimeout(poll, 700); }
async function pollLog(){ try{ const r=await fetch('/api/log'); const j=await r.json(); const el=document.getElementById('log'); el.textContent=j.lines.map(l=>l[0]+'  '+l[1]).join('\n'); el.scrollTop=el.scrollHeight; }catch(e){} setTimeout(pollLog, 1500); }
pollLog();
document.getElementById('pages2').innerHTML='<div class="vempty">추출이 끝나면 원본 PDF가 여기에 표시됩니다</div>';
"""

KEROL_JS = r"""
let inbox=null; try{ inbox=JSON.parse(localStorage.getItem('kerol_so_inbox')||'null'); }catch(e){}
const COLS=[["Status","status"],["No.","no"],["HAWB No","HBL No"],["MAWB No","MBL No"],["To Flag","toflag"],["EDI Status","edi"],["Service","Service Group"],["BL Type","bltype"],["Flight","Flight / Vessel"],["Shipper","Shipper"],["Shipper Name","shipper_name"],["Consignee","Consignee"],["Consignee Name","consignee_name"],["POL","origin"],["POD","destination"],["Pkg","Package Qty."],["G. Weight","Gross Weight"],["C. Weight","Chargeable Weight"],["Incoterms","Incoterms"],["Inv Amt","Invoice Amt"],["Cur","Invoice Cur"],["Ref No","Ref. No."],["Operator","op"]];
function rowOf(r,i){ return '<tr'+(r._new?' class="new"':'')+'>'+COLS.map(([h,k])=>{ let v=r[k]; if(k==='status') v='Ordered'; if(k==='no') v=i+1; if(k==='toflag') v='N'; if(k==='edi') v='-'; if(k==='bltype') v='HS'; if(k==='op') v='dt'; return '<td>'+esc(v)+'</td>';}).join('')+'</tr>'; }
function renderList(){ const list=JSON.parse(localStorage.getItem('kerol_hawb_list')||'[]'); document.getElementById('gbody').innerHTML=list.map(rowOf).join('')||'<tr><td colspan="23" class="empty">조회 결과가 없습니다</td></tr>';
  document.getElementById('tp').textContent=list.reduce((a,r)=>a+(+r['Package Qty.']||0),0); document.getElementById('tw').textContent=list.reduce((a,r)=>a+(+r['Gross Weight']||0),0).toLocaleString(); document.getElementById('tc').textContent=list.length; }
function insertRow(){ if(!inbox) return; const r=Object.assign({}, inbox.so, {shipper_name: inbox.ex.shipper_name, consignee_name: inbox.ex.consignee_name, origin: inbox.ex.origin, destination: inbox.ex.destination, _new:true});
  const list=JSON.parse(localStorage.getItem('kerol_hawb_list')||'[]'); list.forEach(x=>delete x._new); list.unshift(r); localStorage.setItem('kerol_hawb_list',JSON.stringify(list)); localStorage.removeItem('kerol_so_inbox');
  document.getElementById('toast').textContent=`HAWB ${r['HBL No']||''} 등록 완료`; document.getElementById('toast').style.display='block'; renderList(); setTimeout(()=>{document.getElementById('toast').style.display='none';},4000); }
document.getElementById('clear').onclick=()=>{localStorage.removeItem('kerol_hawb_list');renderList();};
renderList(); if(inbox){ document.getElementById('inserting').style.display='block'; setTimeout(()=>{document.getElementById('inserting').style.display='none'; insertRow();}, 900); }
"""


def page(title, body, js, extra_head="", cls=""):
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600&display=swap">{extra_head}<style>{CSS}</style></head>
<body class="{cls}">{body}<script>{js}</script></body></html>"""


def confirm_html(data, cap=None, hynix=False, other_url=None):
    import diagnostics_view
    stc = collections.Counter(diagnostics_view.status(c) for c in data)
    dg = diagnostics_view.build(data)
    vbar = lambda pid: f'<div class="vbar"><button onclick="zoom(-1)">축소</button><button onclick="zoom(1)">확대</button><button onclick="zoom(0)">맞춤</button><span class="zl mono">100%</span></div><div class="pages" id="{pid}"></div>'
    ed = ('<span class="ed hy">하이닉스 중국법인 판, GERP 품목 표</span>' if hynix
          else '<span class="ed">FSK 판, KEROL SO</span>')
    if other_url:
        ed += f'<a class="swap" href="{other_url}">{"FSK 판으로" if hynix else "하이닉스 판으로"} →</a>'
    body = f"""<header><h1>DocExtract</h1>{ed}<span class="sub">전체 <b class="mono">{len(data)}</b>건 | 오류 <b class="mono hn bad" id="h-bad">{stc['bad']}</b>건 | 확인 <b class="mono hn ask" id="h-ask">{stc['ask']}</b>건 | 정상 <b class="mono hn ok" id="h-ok">{stc['ok']}</b>건</span>
<div class="tabs"><button class="on" data-v="v-confirm">검토</button><button data-v="v-new">서류 등록</button><button data-v="v-diag">Diagnostics</button>{'<button data-v="v-cap">Throughput</button>' if cap else ''}</div></header>
<div class="view on" id="v-confirm"><div class="list"><input id="q" placeholder="HBL 번호, 송하인"><div class="filters"><button data-f="all" class="on">전체</button><button data-f="AIR">AIR</button><button data-f="SEA">SEA</button><button data-f="ok">정상</button><button data-f="ask">확인</button><button data-f="bad">오류</button><span id="count" style="align-self:center;color:var(--ink-3);font-size:12px"></span></div><div id="rows"></div></div>
<div class="detail" id="detail"></div><div class="viewer">{vbar('pages')}</div></div>
<div class="view" id="v-new"><div class="detail up">
<div class="card"><h2>서류 올리기 <span>PDF, 여러 개 가능</span></h2>
<label class="dz" id="dz"><input type="file" id="files" multiple accept="application/pdf"><b>PDF를 끌어다 놓거나 눌러서 선택하십시오</b><span>한 화물의 BL, CI, PL을 함께 올리면 한 건으로 추출합니다</span></label>
<ul class="flist" id="flist"></ul>
<div class="uact"><button class="btn" id="go" disabled>추출 시작</button><button class="btn ghost" id="clr">비우기</button></div></div>
<div class="card"><h2>진행 <span class="pill idle" id="jstat">대기</span></h2>
<div class="clockrow"><span class="clock mono" id="clock">0.0초</span><span class="clk-sub" id="clksub">파일을 올리면 시작할 수 있습니다</span></div>
<ol class="tl" id="steps"></ol><div class="sumg" id="sumg"></div></div>
<details class="card devlog"><summary>개발자 로그 <span>CLI 명령과 호출별 소요 시간</span></summary><pre id="log"></pre></details>
<div id="result2"></div></div><div class="viewer">{vbar('pages2')}</div></div>"""
    body += diagnostics_view.html(dg)
    cap_js = ""
    if cap:
        import capacity_view
        cap_body, cap_js = capacity_view.html(cap)
        body += cap_body
    def _js(v, **kw):
        return json.dumps(v, **kw).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    js = (APP_JS.replace("__DATA__", _js(data, ensure_ascii=False)).replace("__SO_COLS__", _js(docpipe.SO_COLS, ensure_ascii=False))
          .replace("__REQUIRED__", _js(REQUIRED)).replace("__OPTIONAL__", _js(OPTIONAL)).replace("__LABELS__", _js(LABELS, ensure_ascii=False))
          .replace("__RULE_FIELDS__", json.dumps(RULE_FIELDS)))
    rule_names = {r["key"]: r["label"] for r in dg["rules"]}
    js = js.replace("__RULE_NAMES__", json.dumps(rule_names, ensure_ascii=False)) + diagnostics_view.JS
    # 🔴 pdf.js 를 서버가 같이 낸다. CDN 에서만 받게 두면 인터넷이 막힌 자리에서 원본 창이
    #    통째로 빈다. 시연 현장과 녹화 브라우저 둘 다 실제로 그렇게 됐고, 영상에서 오른쪽
    #    창이 흰 채로 찍혔다. 로컬 파일이 없으면 CDN 으로 물러선다.
    head = ('<script src="assets/pdf.min.js"></script>'
            '<script>if(!window.pdfjsLib){window.__PDFJS_LOCAL=0;'
            'document.write(unescape("%3Cscript src=\'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js\'%3E%3C/script%3E"))}'
            'else{window.__PDFJS_LOCAL=1}</script>')
    head += f"<style>{diagnostics_view.CSS}</style>"
    if cap:
        import capacity_view
        head += f"<style>{capacity_view.CSS}</style>"
        js += cap_js
    return page("DocExtract" + (" 하이닉스 판" if hynix else ""), body, js, head, "hyedition" if hynix else "")


KEROL_CSS = """
.k *{box-sizing:border-box}.k{font-family:"Noto Sans KR","IBM Plex Sans KR",system-ui,sans-serif;font-size:13px;color:#333;background:#f6f7f9;min-height:100vh;display:grid;grid-template-columns:86px 1fr}
.k .side{background:#1d4f9f;min-height:100vh;display:flex;flex-direction:column;align-items:center;gap:26px;padding-top:14px;color:#fff}
.k .side .home{width:86px;height:56px;background:#163f82;display:flex;align-items:center;justify-content:center}.k .side svg{opacity:.92}
.k .top{height:56px;background:#fff;border-bottom:1px solid #d7dde6;display:flex;align-items:center;gap:26px;padding:0 24px}
.k .logo{font-weight:700;font-size:22px;letter-spacing:1px;color:#1a2b4c;border-bottom:4px solid;border-image:linear-gradient(90deg,#d9342b 0 30%,#9aa3ad 30% 60%,#f2a900 60% 100%) 1}
.k .nav{display:flex;gap:26px;color:#1f4e9a;font-size:15px}.k .nav b{background:#1d4f9f;color:#fff;padding:3px 14px;border-radius:14px;font-weight:500}
.k .user{margin-left:auto;color:#333;font-size:13px}.k .tabbar{background:#f6f7f9;padding:6px 24px 0;display:flex;gap:4px}.k .tab{background:#9aa5b6;color:#fff;padding:5px 14px;border-radius:6px 6px 0 0;font-size:13px}.k .tab.on{background:#1f9bd6}
.k .title{display:flex;align-items:center;justify-content:space-between;padding:12px 26px;background:#eaf1f8;border-bottom:2px solid #1d4f9f}.k .title h1{font-size:30px;font-weight:400;margin:0;color:#222}
.k .crumb{color:#666;font-size:14px;text-align:right}.k .crumb b{color:#e58a1f;font-weight:500}.k .btns{display:flex;gap:6px;margin-top:6px}.k .b{background:#1f9bd6;color:#fff;padding:7px 18px;border-radius:5px;font-size:14px;border:0;cursor:pointer}
.k .filter{margin:14px 24px 0;border:1px solid #d7dde6;background:#fff;display:grid;grid-template-columns:repeat(3,1fr)}.k .filter div{display:grid;grid-template-columns:110px 1fr;align-items:center;border-bottom:1px solid #e6ebf1;border-right:1px solid #e6ebf1;padding:7px 10px;gap:8px}
.k .filter label{color:#444;text-align:right;padding-right:10px}.k .filter span{display:inline-block;border:1px solid #cfd6df;padding:4px 8px;min-width:150px;color:#666;background:#fff;border-radius:2px}
.k .tool{display:flex;justify-content:flex-end;gap:6px;margin:12px 24px}.k .tool .b{background:#3aa6db;padding:6px 14px}
.k table{width:calc(100% - 48px);margin:0 24px;border-collapse:collapse;background:#fff;border:1px solid #d7dde6;font-size:12.5px}.k th{background:#eef2f7;color:#444;font-weight:500;padding:8px 6px;border-bottom:1px solid #d7dde6;white-space:nowrap}
.k td{padding:7px 6px;border-bottom:1px solid #eef1f5;white-space:nowrap}.k tr.new td{background:#fff5cc;animation:flash 2.5s ease-out}
@keyframes flash{0%{background:#ffd54a}100%{background:#fff5cc}}.k .foot{display:flex;gap:40px;margin:10px 24px;padding:10px;background:#eef2f7;color:#444}
.k .empty{color:#888;text-align:center}.k #toast{display:none;position:fixed;right:24px;top:70px;background:#1d4f9f;color:#fff;padding:12px 18px;border-radius:6px;box-shadow:0 4px 14px rgba(0,0,0,.25)}
.k #inserting{display:none;margin:0 24px;padding:10px;background:#fff8e1;border:1px solid #f2d27a;color:#7a5a00}
"""


def kerol_html():
    ic = lambda d: f'<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{d}</svg>'
    side = "".join(ic(d) for d in (
        '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
        '<rect x="3" y="4" width="14" height="16" rx="1.5"/><path d="M7 9h6M7 13h6"/><circle cx="17" cy="16" r="3.5"/><path d="m19.5 18.5 2 2"/>',
        '<rect x="5" y="4" width="14" height="17" rx="1.5"/><path d="M9 3h6v3H9zM8 12l2.5 2.5L16 9"/>',
        '<path d="M5 4h10l4 4v12H5z"/><path d="M8 13h6M8 17h8"/><path d="m14 4v4h4"/>',
        '<path d="M3 17c2 1.5 4 1.5 6 0 2 1.5 4 1.5 6 0 2 1.5 4 1.5 6 0"/><path d="M4 13 6 7h12l2 6"/><path d="M9 7V4h6v3"/>',
        '<path d="M4 21V5l8-2v18M12 9l8 2v10"/><path d="M7 8h2M7 12h2M7 16h2M15 14h2M15 18h2"/>',
        '<path d="M3 7h11v9H3zM14 10h4l3 3v3h-7"/><circle cx="7" cy="18" r="1.8"/><circle cx="17" cy="18" r="1.8"/>',
        '<rect x="4" y="4" width="16" height="16" rx="1.5"/><path d="M8 9h8M8 13h8M8 17h5"/><path d="M15 3v3M9 3v3"/>',
        '<path d="M12 21s6-5.5 6-11a6 6 0 0 0-12 0c0 5.5 6 11 6 11z"/><circle cx="12" cy="10" r="2.2"/>',
        '<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M12 3v9h9"/>',
    ))
    top_icons = "".join(f'<span style="color:{c};display:inline-flex;vertical-align:middle;margin-left:6px">{ic(d)}</span>' for c, d in (
        ("#f28c1a", '<path d="M14 6 6 14M9 4l2 2M5 8l2 2"/><path d="m13 11 6 6-2 2-6-6z"/>'),
        ("#3aa6db", '<path d="M4 5h16v11H9l-5 4z"/>'),
        ("#3aa6db", '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>'),
        ("#9b59b6", '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>'),
        ("#d9342b", '<path d="M12 3v9"/><path d="M6.5 6.5a8 8 0 1 0 11 0"/>')))
    body = f"""<div class="k"><div class="side"><div class="home">{ic('<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>')}</div>{side}</div>
<div><div class="top"><span class="logo">KEROL</span><span style="color:#f28c1a">★</span><span class="nav"><span>Schedule</span><span>Booking</span><span>Shipping</span><b>Arrival</b><span>Customs</span><span>Transport</span><span>Mana…</span></span><span class="user">FSK L&amp;S Co., Ltd. A301 | dt {top_icons}</span></div>"""
    body += """
<div class="tabbar"><span class="tab">HBL List</span><span class="tab on">HAWB List</span></div>
<div class="title"><div><div style="width:40px;height:3px;background:#1f9bd6;margin-bottom:8px"></div><h1>HAWB List (In)</h1></div><div><div class="crumb">Forwarding &gt; Arrival &gt; Air &gt; <b>HAWB List</b></div><div class="btns"><button class="b">Search</button><button class="b">Refresh</button><button class="b">Excel</button><button class="b">Open</button><button class="b">Close</button><button class="b">EDI Link</button><button class="b">Email</button></div></div></div>
<div class="filter"><div><label>Organization</label><span>Select…</span></div><div><label>Date</label><span>ETA &nbsp; 2026-09-10 ~ 2026-09-24</span></div><div><label>Ref. No.</label><span>HBL No</span></div>
<div><label>Service</label><span>Select…</span></div><div><label>Party</label><span>Select…</span></div><div><label>Detail Ref.</label><span>INVOICE NO</span></div>
<div><label>BL Type</label><span>Select…</span></div><div><label>Party</label><span>Select…</span></div><div><label>Location</label><span>POL</span></div>
<div><label>Salesman</label><span>&nbsp;</span></div><div><label>Operator</label><span>dt</span></div><div><label>Location</label><span>POD</span></div>
<div><label>Order Status</label><span>Select…</span></div><div></div><div></div></div>
<div id="inserting">등록 중</div>
<div class="tool"><button class="b">Memo Save</button><button class="b">Attach Multi Delete</button><button class="b">HBL multi Download</button><button class="b">HBL Multi Upload</button><button class="b">Weight Upload</button><button class="b" id="clear" style="background:#9aa5b6">초기화</button></div>
<div style="overflow:auto"><table><thead><tr><th>Status</th><th>No.</th><th>HAWB No</th><th>MAWB No</th><th>To Flag</th><th>EDI Status</th><th>Service</th><th>BL Type</th><th>Flight</th><th>Shipper</th><th>Shipper Name</th><th>Consignee</th><th>Consignee Name</th><th>POL</th><th>POD</th><th>Pkg</th><th>G. Weight</th><th>C. Weight</th><th>Incoterms</th><th>Inv Amt</th><th>Cur</th><th>Ref No</th><th>Operator</th></tr></thead><tbody id="gbody"></tbody></table></div>
<div class="foot"><span>Total Package : <b id="tp">0</b></span><span>Total G. Weight : <b id="tw">0</b></span><span>Total Count : <b id="tc">0</b></span><span style="margin-left:auto"><a href="confirm.html" style="color:#1f4e9a">검토 화면</a></span></div>
<p style="margin:6px 24px;color:#888;font-size:12px">시연용 화면</p>
<div id="toast"></div></div></div>"""
    return page("KEROL HAWB List (가상)", body, KEROL_JS, f"<style>{KEROL_CSS}</style>")


def write_ui(recs, others, root, out, docs_prefix="docs", cap=None, hynix=False, other_url=None):
    """recs 는 결과 목록이거나 (결과 목록, 원본 폴더) 쌍의 목록이다. 항공과 해상을 한 화면에 담는다."""
    os.makedirs(out, exist_ok=True)
    groups = recs if recs and isinstance(recs[0], tuple) else [(recs, root)]
    from concurrent.futures import ThreadPoolExecutor
    jobs = [(r, rt) for rs, rt in groups for r in rs if r.get("raw") and not r.get("error")]
    with ThreadPoolExecutor(max_workers=6) as pool:
        data = list(pool.map(lambda x: build_case(x[0], others.get(x[0].get("case")), x[1], docs_prefix, hynix), jobs))
    with open(os.path.join(out, "confirm.html"), "w", encoding="utf-8") as f:
        f.write(confirm_html(data, cap, hynix, other_url))
    with open(os.path.join(out, "kerol_mock.html"), "w", encoding="utf-8") as f:
        f.write(kerol_html())
    return len(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description="확인 화면과 가상 KEROL 화면을 HTML 로 낸다.")
    ap.add_argument("--pred", required=True, nargs="+", help="결과 폴더. 여럿이면 --root 도 같은 순서로 준다")
    ap.add_argument("--compare"); ap.add_argument("--root", nargs="+", help="케이스 폴더. 원본 PDF 를 찾는다")
    ap.add_argument("--out", required=True); ap.add_argument("--limit", type=int, default=0, help="앞의 몇 건만 (시험용)")
    ap.add_argument("--docs-prefix", default="docs", help="원본 PDF 주소의 앞부분. 하위 폴더에 낼 때는 /docs")
    ap.add_argument("--capacity", default=os.path.join(__import__("wuxi_paths").WORK, "out", "capacity_stats.json"), help="처리 용량 화면에 쓸 배치 통계")
    ap.add_argument("--hynix", action="store_true", help="하이닉스 중국법인 판. 품목 줄 카드와 엑셀 내려받기를 더한다")
    ap.add_argument("--other-url", help="다른 판의 주소. 머리글에 전환 링크를 단다")
    a = ap.parse_args(argv)
    roots = [os.path.abspath(os.path.expanduser(r)) for r in (a.root or [""] * len(a.pred))]
    groups = []
    for pr, rt in zip(a.pred, roots):
        rs = load_preds(os.path.abspath(os.path.expanduser(pr)))
        groups.append((rs[:a.limit] if a.limit else rs, rt))
    others = {r.get("case"): r for r in load_preds(os.path.abspath(os.path.expanduser(a.compare)))} if a.compare else {}
    import capacity_view
    n = write_ui(groups, others, "", os.path.abspath(os.path.expanduser(a.out)), a.docs_prefix,
                 capacity_view.load(a.capacity), a.hynix, a.other_url)
    print(f"케이스 {n}건 → {a.out}/confirm.html, kerol_mock.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
