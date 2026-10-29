"""우시 판독 엔진의 입구. 화면(wuxi_web.py), 설치 점검(selfcheck.py)이 같은 길로 부른다.

    read_bundle(pdfs)    쪽 가르기 → 스캔이면 OCR → 서류별 AI 판독 → 후처리, 검증 → 빠짐 검사
    payload(job, res)    화면에 줄 값(G-ERP 표, 인보이스 합계, 검증, 빠짐 검사, 원본 위치)

🔴 빠짐 검사(completeness)를 따로 두는 까닭. 고객이 받는 G-ERP 품목 표는 서류의 품목 줄이 하나도 빠지면 안 된다.
   AI 가 긴 표의 뒷쪽을 건너뛰거나 쪽이 넘어가는 자리의 줄을 빠뜨려도 모델 답만 보면 알 길이 없다. 그래서 모델과
   상관없는 근거 넷으로 따로 센다. 하나라도 어긋나면 화면 맨 위에 「빠진 줄 의심」을 띄우고 사람이 원본과 대조하게 한다.
     1. 인보이스마다 줄 금액 합 = 인쇄된 총액
     2. 인보이스 줄 수 = 포장명세서 줄 수 = 통관표 줄 수(둘 다 있을 때)
     3. 글자층이 있는 인보이스 쪽마다 읽은 줄이 하나 이상(뒷쪽 통째 빠짐)
     4. 글자층에 찍힌 PO 품목 번호, SAP 코드 가운데 읽은 줄에 없는 것
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import docpipe  # noqa: E402
import gerp_xlsx  # noqa: E402
import wuxi_paths  # noqa: E402

MODEL = os.environ.get("MODEL") or "claude-sonnet-5"
LOCK = threading.Lock()          # 엔진의 모듈 전역(set_prompt, 표 정렬)을 지키려고 판독은 한 번에 하나
_local = threading.local()
KIND_KO = {"AWB": "운송장", "CI": "인보이스", "PL": "포장명세서", "CUSTOMS": "통관 용도설명서", "TERMS": "약관",
           "OTHER": "기타", "SCAN": "스캔", "LCA": "운임표", "PO": "구매 주문서", "INTERNAL": "내부 문서"}

_real_call = docpipe.call_claude_cli


def _logged_call(model, messages, paths=None, timeout=300, binary="claude", think=False, config_dir=None, schema=None):
    """AI 호출마다 화면에 단계를 남긴다(무엇을 읽는 중인지)."""
    on_step = getattr(_local, "on_step", None)
    keys = set((schema or docpipe.SCHEMA_STRICT).get("properties", {}))
    kind = ("운송장" if "awb_pieces" in keys and "ci_lines" not in keys else
            "인보이스" if "ci_lines" in keys and "pl_lines" not in keys else
            "포장명세서" if "pl_lines" in keys and "ci_lines" not in keys else
            "통관 용도설명서" if "cn_lines" in keys and "ci_lines" not in keys else
            "인보이스와 포장명세서" if "ci_lines" in keys else "스캔 전체")
    n_img = sum(1 for x in (paths or []) if str(x).lower().endswith(".png"))
    text = f"{kind} 읽기" + (f" (그림 {n_img}쪽)" if n_img else "")
    if on_step:
        on_step(text, False, None)
    t0 = time.perf_counter()
    try:
        return _real_call(model, messages, paths=paths, timeout=timeout, binary=binary, think=think,
                          config_dir=config_dir, schema=schema)
    finally:
        if on_step:
            on_step(text, True, time.perf_counter() - t0)


docpipe.call_claude_cli = _logged_call


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def completeness(ex: dict, pages) -> dict:
    """모델 답과 상관없는 근거로 빠진 줄을 의심한다. verdict: 이상 없음 | 빠진 줄 의심 | 확인 못 함."""
    ci = [x for x in (ex.get("ci_lines") or []) if isinstance(x, dict)]
    pl = [x for x in (ex.get("pl_lines") or []) if isinstance(x, dict)]
    cn = [x for x in (ex.get("cn_lines") or []) if isinstance(x, dict)]
    notes, bad, checked = [], 0, 0
    # 1. 인보이스마다 줄 금액 합과 인쇄된 총액
    tax = _num(ex.get("ci_tax")) or 0
    for t in ex.get("invoice_totals") or []:
        if not isinstance(t, dict) or _num(t.get("total")) is None:
            continue
        inv = str(t.get("invoice_no") or "")
        rows = [x for x in ci if str(x.get("invoice_no") or "") == inv] if inv and len(ex["invoice_totals"]) > 1 else ci
        amts = [_num(x.get("amount")) for x in rows]
        if not rows or any(a is None for a in amts):
            continue
        checked += 1
        s, total = round(sum(amts), 2), _num(t["total"])
        if abs(s - total) > 0.011 and abs(s + tax - total) > 0.011:
            bad += 1
            notes.append(f"인보이스 {inv or ''} 줄 금액 합 {s:,.2f} 이 총액 {total:,.2f} 와 다름({len(rows)}줄)")
    # 2. 서류끼리 줄 수
    if ci and pl:
        checked += 1
        if len(pl) != len(ci):
            bad += 1
            notes.append(f"인보이스 {len(ci)}줄, 포장명세서 {len(pl)}줄로 줄 수가 다름")
    if ci and cn:
        checked += 1
        if len(cn) != len(ci):
            bad += 1
            notes.append(f"인보이스 {len(ci)}줄, 통관 용도설명서 {len(cn)}줄로 줄 수가 다름")
    # 3, 4. 글자층이 있는 인보이스 쪽
    seen_codes = {str(x.get(k) or "").strip().upper() for x in ci for k in ("sap_code", "material_code", "part_no")} - {""}
    seen_items = {(str(x.get("po_no") or ""), str(x.get("po_item_no") or "")) for x in ci}
    ci_pages = [p for p in pages if getattr(p, "kind", "") == "CI" and (getattr(p, "text", "") or "").strip()]
    empty_pages = []
    for p in ci_pages:
        t = p.text.upper()
        if not any(c and c in t for c in seen_codes) and not any(
                it and it in t and po in t for po, it in seen_items if po):
            empty_pages.append(getattr(p, "no", None) or getattr(p, "page", None) or "?")
    if ci_pages:
        checked += 1
        if empty_pages:
            bad += 1
            notes.append(f"인보이스 쪽 {len(empty_pages)}개에서 읽은 줄이 하나도 확인되지 않음")
    if not checked:
        return {"verdict": "확인 못 함", "notes": ["총액, 줄 수, 글자층 가운데 견줄 근거가 없습니다. 원본과 줄 수를 직접 대조하십시오."],
                "lines": len(ci)}
    return {"verdict": "빠진 줄 의심" if bad else "이상 없음",
            "notes": notes or [f"총액, 줄 수, 쪽 근거 {checked}가지가 모두 맞습니다"], "lines": len(ci)}


def read_bundle(pdfs, name="올린 서류", on_step=None, model=None, claude_bin=None, config_dir=None) -> dict:
    """서류 묶음 하나를 읽는다. on_step(text, done, sec) 으로 진행을 알린다."""
    def step(text, done=False, sec=None):
        if on_step:
            on_step(text, done, sec)

    with LOCK:
        _local.on_step = on_step
        try:
            t0 = time.perf_counter()
            docpipe.set_prompt("h1")
            step(f"서류 {len(pdfs)}개 쪽 가르기")
            pages = docpipe.apply_awb_sort([docpipe.classify(p) for path in pdfs for p in docpipe.load_pdf(path)])
            n_scan = sum(1 for p in pages if p.kind == "SCAN")
            if n_scan:
                d = docpipe.ocr_pages(pages)
                docpipe.route_scans(pages)
                if d:
                    shutil.rmtree(d, ignore_errors=True)
            kinds = [p.kind for p in pages]
            cnt = {}
            for k in kinds:
                cnt[KIND_KO.get(k, k)] = cnt.get(KIND_KO.get(k, k), 0) + 1
            step(f"서류 {len(pdfs)}개 쪽 가르기", True, time.perf_counter() - t0)
            step(f"모두 {len(pages)}쪽: " + ", ".join(f"{k} {v}쪽" for k, v in cnt.items())
                 + (f" (스캔 {n_scan}쪽은 OCR 로 먼저 읽음)" if n_scan else ""), True, 0)
            rows = docpipe.run([(name, list(pdfs))], os.path.join(wuxi_paths.SCHEMA, "codes.json"),
                               model=model or MODEL, show=False, masters=docpipe.load_masters(), claude_cli=True,
                               claude_bin=claude_bin or wuxi_paths.claude_bin(),
                               claude_config_dir=config_dir if config_dir is not None else wuxi_paths.claude_config_dir())
            r = rows[0]
            llm = r.get("llm") or {}
            if llm.get("error"):
                raise RuntimeError(llm["error"])
            ex = r.get("ex") or docpipe.postprocess(r.get("raw") or {})
            step("숫자 검증과 빠짐 검사", True, 0)
            return {"ex": ex, "verify": r.get("verify") or [], "kinds": kinds, "n_scan": n_scan,
                    "calls": llm.get("calls"), "usd": llm.get("api_usd"), "wall": round(time.perf_counter() - t0, 1),
                    "completeness": completeness(ex, pages)}
        finally:
            _local.on_step = None


def friendly_error(msg: str) -> str:
    if re.search(r"rate limit|usage limit|429", msg, re.I):
        return "AI 사용 한도에 걸렸습니다. 잠시 뒤 다시 올려 주십시오."
    if re.search(r"ENOTFOUND|ECONNRESET|ETIMEDOUT|network", msg, re.I):
        return "AI 서버에 연결하지 못했습니다. 잠시 뒤 다시 올려 주십시오."
    if re.search(r"보낼 쪽이 없습니다", msg):
        return "읽을 서류 쪽을 찾지 못했습니다. 인보이스나 포장명세서가 들어 있는지 확인해 주십시오."
    return msg[:300]


def _find_rects(docs, text):
    q = str(text or "").strip()
    if len(q) < 3:
        return []
    hits = []
    for di, doc in enumerate(docs):
        if doc is None:
            continue
        for pi, page in enumerate(doc):
            try:
                rects = page.search_for(q)
            except Exception:
                rects = []
            for r in rects[:2]:
                hits.append([di, pi + 1, round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)])
            if len(hits) >= 4:
                return hits
    return hits


def payload(name, pdfs, res) -> dict:
    """화면에 줄 값. 줄마다 원본에서 찾을 자리(_hl)를 붙인다."""
    import pymupdf
    rec = {"case": name, "ex": res["ex"], "verify": res["verify"]}
    t = gerp_xlsx.tables_for([rec])
    docs = []
    for p in pdfs:
        try:
            docs.append(pymupdf.open(p))
        except Exception:
            docs.append(None)
    sizes = [[[round(pg.rect.width), round(pg.rect.height)] for pg in d] if d else [] for d in docs]
    for row in t["items"]:
        marks = []
        for col in ("Material", "Vendor Part No.", "Purchase Order", "Invoice amount"):
            marks += _find_rects(docs, row.get(col))
            if marks:
                break
        row["_hl"] = marks[:4]
    for d in docs:
        if d:
            d.close()
    checks = [{"grade": g, "item": k, "text": m} for g, k, m in res["verify"]]
    return {
        "head": t["head"][0] if t["head"] else {},
        "items": t["items"],
        "inv": t["inv"],
        "review": t["review"][0] if t["review"] else {},
        "req": t.get("req") or [],
        "checks": checks,
        "counts": {g: sum(1 for c in checks if c["grade"] == g) for g in ("OK", "WARN", "FAIL")},
        "completeness": res["completeness"],
        "cols": {"gerp": gerp_xlsx.GERP_ITEM_COLS, "gerp_only": gerp_xlsx.GERP_ONLY, "extra": gerp_xlsx.EXTRA_ITEM_COLS,
                 "head": gerp_xlsx.GERP_HEAD_COLS, "inv": gerp_xlsx.INV_COLS},
        "sizes": sizes,
        "kinds": res["kinds"],
        "llm": {"calls": res.get("calls"), "usd": res.get("usd"), "wall": res.get("wall")},
    }
