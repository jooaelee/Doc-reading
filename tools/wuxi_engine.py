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
import wuxi_rules  # noqa: E402

MODEL = wuxi_paths.model()   # 화면에서 바꾸면 read_bundle 이 부를 때마다 다시 읽는다
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


def completeness(ex: dict, pages, policy: dict | None = None) -> dict:
    """모델 답과 상관없는 근거로 빠진 줄을 의심한다. verdict: 이상 없음 | 빠진 줄 의심 | 확인 못 함.

    policy 는 wuxi_rules.check_policy() 의 꼴이다. 꺼 둔 검사는 세지 않고 결과에 「꺼짐」으로 남긴다.
    """
    pol = policy or wuxi_rules.check_policy(None)
    ci = [x for x in (ex.get("ci_lines") or []) if isinstance(x, dict)]
    pl = [x for x in (ex.get("pl_lines") or []) if isinstance(x, dict)]
    cn = [x for x in (ex.get("cn_lines") or []) if isinstance(x, dict)]
    checks = []

    def add(key, state, note=""):
        checks.append({"check": key, "name": wuxi_rules.CHECKS[key][0], "state": state, "note": note,
                       "rule": (pol.get(key) or {}).get("rule")})

    # 1. 인보이스마다 줄 금액 합과 인쇄된 총액
    if not pol["amount"]["enabled"]:
        add("amount", "off")
    else:
        tol = float(pol["amount"].get("tolerance") or 0.01) + 0.001
        tax = _num(ex.get("ci_tax")) or 0
        seen, bad_notes = 0, []
        totals = [t for t in ex.get("invoice_totals") or [] if isinstance(t, dict)]
        for t in totals:
            if _num(t.get("total")) is None:
                continue
            inv = str(t.get("invoice_no") or "")
            rows = [x for x in ci if str(x.get("invoice_no") or "") == inv] if inv and len(totals) > 1 else ci
            amts = [_num(x.get("amount")) for x in rows]
            if not rows or any(a is None for a in amts):
                continue
            seen += 1
            sm, total = round(sum(amts), 2), _num(t["total"])
            if abs(sm - total) > tol and abs(sm + tax - total) > tol:
                bad_notes.append(f"인보이스 {inv or ''} 줄 금액 합 {sm:,.2f}이 총액 {total:,.2f}와 다릅니다({len(rows)}줄)")
        if not seen:
            add("amount", "skip", "총액이 찍힌 인보이스가 없어 견주지 못했습니다")
        elif bad_notes:
            add("amount", "bad", " / ".join(bad_notes))
        else:
            add("amount", "ok", f"인보이스 {seen}장의 금액 합이 총액과 같습니다")
    # 2, 3. 서류끼리 줄 수
    for key, other, label in (("lines_pl", pl, "포장명세서"), ("lines_cn", cn, "통관 용도설명서")):
        if not pol[key]["enabled"]:
            add(key, "off")
        elif not (ci and other):
            add(key, "skip", f"{label}가 없어 견주지 못했습니다" if ci else "인보이스 줄이 없습니다")
        elif len(other) != len(ci):
            add(key, "bad", f"인보이스 {len(ci)}줄, {label} {len(other)}줄로 줄 수가 다릅니다")
        else:
            add(key, "ok", f"인보이스와 {label} 모두 {len(ci)}줄입니다")
    # 4. 글자층이 있는 인보이스 쪽마다 읽은 줄
    if not pol["pages"]["enabled"]:
        add("pages", "off")
    else:
        seen_codes = {str(x.get(k) or "").strip().upper() for x in ci for k in ("sap_code", "material_code", "part_no")} - {""}
        seen_items = {(str(x.get("po_no") or ""), str(x.get("po_item_no") or "")) for x in ci}
        ci_pages = [p for p in pages if getattr(p, "kind", "") == "CI" and (getattr(p, "text", "") or "").strip()]
        empty_pages = []
        for p in ci_pages:
            t = p.text.upper()
            if not any(c and c in t for c in seen_codes) and not any(
                    it and it in t and po in t for po, it in seen_items if po):
                empty_pages.append(getattr(p, "no", None) or "?")
        if not ci_pages:
            add("pages", "skip", "글자가 있는 인보이스 쪽이 없어 견주지 못했습니다")
        elif empty_pages:
            add("pages", "bad", f"인보이스 쪽 {len(empty_pages)}개에서 읽은 줄이 하나도 확인되지 않습니다")
        else:
            add("pages", "ok", f"인보이스 {len(ci_pages)}쪽 모두 읽은 줄이 보입니다")
    bad = [c for c in checks if c["state"] == "bad"]
    ok = [c for c in checks if c["state"] == "ok"]
    if bad:
        verdict, notes = "빠진 줄 의심", [c["note"] for c in bad]
    elif ok:
        verdict, notes = "이상 없음", [f"총액, 줄 수, 쪽 근거 {len(ok)}가지가 모두 맞습니다"]
    else:
        verdict = "확인 못 함"
        notes = ["총액, 줄 수, 글자층 가운데 견줄 근거가 없습니다. 원본과 줄 수를 직접 대조하십시오."]
    return {"verdict": verdict, "notes": notes, "lines": len(ci), "checks": checks}


# 서류 머리의 PO. 라벨 뒤 두 줄 안에 오는 하이닉스 PO 모양(43 이나 45 로 시작하는 10자리. 정답에 적힌 PO 246칸 중 213칸)만 본다
_PO_LABEL = re.compile(r"(?i)\b(?:p\s*/\s*o|p\.\s*o\.?|po|purchase\s+order)(?![a-z])\s*(?:no\.?|number|#)?\s*[:.]?")
_PO_VALUE = re.compile(r"(?<![0-9A-Za-z])(4[35]\d{8})(?!\d)")


def header_po(ex: dict, text: str) -> str | None:
    """줄에 PO 가 하나도 없고 서류 머리에 하이닉스 PO 가 하나만 찍혀 있으면 모든 품목 줄에 그 PO 를 넣는다. 넣은 값을 돌려준다.

    🔴 docpipe.hynix_fill 에 같은 생각이 있지만 우시 판독 길에서는 불리지 않았다(그 함수는 코드 지우기까지 같이 한다).
       ENF(「9) Your PO No.」 아래 「Attn:」 줄 다음 번호), YCCHEM(「CUSTOMER P/O NO:」 다음 줄),
       SAM YOUNG(화인 칸 「* PO NO. 4353568221」)은 PO 가 표 밖 머리에만 있어 10월 정답 12건에서 PO 칸이 모두 비었다(11/12).
       ENF 운송장의 「* PO NO.: 150909261002」 는 인보이스 번호라 모양으로 거른다.
    """
    ci = [x for x in (ex.get("ci_lines") or []) if isinstance(x, dict)]
    lines = ci + [x for x in (ex.get("pl_lines") or []) if isinstance(x, dict)]
    # 인보이스 줄만 본다. AI 가 운송장의 「* PO NO.」(ENF 인보이스 번호)를 포장명세서 줄에 넣는 일이 있다(11/12)
    if not lines or any(str(x.get("po_no") or "").strip() for x in (ci or lines)):
        return None
    found = []
    for m in _PO_LABEL.finditer(text or ""):
        tail = text[m.end():m.end() + 120]
        tail = "\n".join(tail.split("\n")[:3])          # 라벨 줄과 그 아래 두 줄
        v = _PO_VALUE.search(tail)
        if v and v.group(1) not in found:
            found.append(v.group(1))
    if len(found) != 1:
        return None
    for x in lines:
        if not str(x.get("po_no") or "").strip():
            x["po_no"] = found[0]
            x["_po_from"] = "서류 머리"
    return found[0]


def _pages_text(pages, n=4) -> str:
    """공급사를 알아보는 데 쓰는 앞쪽 글(글자층, 없으면 OCR 글)."""
    out = []
    for p in pages[:n]:
        out.append(getattr(p, "text", "") or getattr(p, "ocr", "") or "")
    return "\n".join(out)


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
            text = _pages_text(pages)
            supplier = wuxi_rules.identify_supplier(text)
            add = wuxi_rules.prompt_addendum(supplier)
            prompt_rules = ([r["id"] for r in wuxi_rules.active("header", supplier) + wuxi_rules.active("guide", supplier)]
                            if add else [])
            if add:
                end = "\nReturn only the JSON object."
                docpipe.SYSTEM = docpipe.SYSTEM.replace(end, add + end) if end in docpipe.SYSTEM else docpipe.SYSTEM + add
                step(f"규칙 {len(prompt_rules)}개를 판독 지침에 더함", True, 0)
            cnt = {}
            for k in kinds:
                cnt[KIND_KO.get(k, k)] = cnt.get(KIND_KO.get(k, k), 0) + 1
            step(f"서류 {len(pdfs)}개 쪽 가르기", True, time.perf_counter() - t0)
            step(f"모두 {len(pages)}쪽: " + ", ".join(f"{k} {v}쪽" for k, v in cnt.items())
                 + (f" (스캔 {n_scan}쪽은 OCR 로 먼저 읽음)" if n_scan else ""), True, 0)
            rows = docpipe.run([(name, list(pdfs))], os.path.join(wuxi_paths.SCHEMA, "codes.json"),
                               model=model or wuxi_paths.model(), show=False, masters=docpipe.load_masters(), claude_cli=True,
                               claude_bin=claude_bin or wuxi_paths.claude_bin(),
                               claude_config_dir=config_dir if config_dir is not None else wuxi_paths.claude_config_dir())
            r = rows[0]
            llm = r.get("llm") or {}
            if llm.get("error"):
                raise RuntimeError(llm["error"])
            ex = r.get("ex") or docpipe.postprocess(r.get("raw") or {})
            if not supplier:
                supplier = wuxi_rules.supplier_for(text, ex)
            all_text = _pages_text([p for p in pages if p.kind != "TERMS"], n=len(pages))
            po = header_po(ex, all_text)
            if po:
                step(f"서류 머리의 PO {po} 값을 품목 줄에 넣음", True, 0)
            trace = wuxi_rules.apply_post(ex, supplier)
            if trace:
                step(f"규칙으로 {len(trace)}칸을 채우거나 고침", True, 0)
            trace += [{"rule": rid, "kind": "prompt", "line": None, "field": None} for rid in prompt_rules]
            step("숫자 검증과 빠짐 검사", True, 0)
            page_rows = [{"file": os.path.basename(getattr(p, "file", "") or ""), "no": getattr(p, "no", None),
                          "kind": p.kind, "scan": bool(getattr(p, "image", False) or p.kind == "SCAN")} for p in pages]
            return {"ex": ex, "verify": r.get("verify") or [], "kinds": kinds, "n_scan": n_scan,
                    "calls": llm.get("calls"), "usd": llm.get("api_usd"), "wall": round(time.perf_counter() - t0, 1),
                    "completeness": completeness(ex, pages, wuxi_rules.check_policy(supplier)),
                    "supplier": supplier, "supplier_name": wuxi_rules.supplier_name(supplier) if supplier else "",
                    "shipper": str(ex.get("shipper_name") or ""), "trace": trace, "pages": page_rows,
                    "model": model or wuxi_paths.model()}
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


# 판독 칸이 표의 어느 열로 나가는지(규칙 표시용)
FIELD_COLS = {"sap_code": ["Material", "Sap Code"], "material_code": ["Material", "Material Code"],
              "hios_no": ["Vendor Part No.", "HIOS No."], "part_no": ["Vendor Part No.", "Part No."],
              "country_of_origin": ["원산지"], "hs_code": ["HS"], "unit": ["단위"]}


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
    # 규칙이 채운 칸을 표에 표시한다(인보이스 줄 i 는 표의 i 번째 줄)
    n_ci = len([x for x in (res["ex"].get("ci_lines") or []) if isinstance(x, dict)])
    for t_ in res.get("trace") or []:
        if t_.get("line") is None or t_["line"] >= min(n_ci, len(t["items"])):
            continue
        row = t["items"][t_["line"]]
        for col in FIELD_COLS.get(t_["field"], []):
            if col == "Material" and row.get("_mat_key") not in (None, t_["field"]):
                continue    # Material 은 다른 칸의 코드를 골랐다(gerp_xlsx.hynix_material)
            row.setdefault("_rules", {})[col] = t_["rule"]
    req = wuxi_rules.required_columns(res.get("supplier"))
    gaps = 0
    for row in t["items"]:
        miss = [" 또는 ".join(g) for g in req if not any(str(row.get(c) or "").strip() for c in g)]
        row["_gap"] = miss
        gaps += bool(miss)
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
        "llm": {"calls": res.get("calls"), "usd": res.get("usd"), "wall": res.get("wall"), "model": res.get("model")},
        "supplier": res.get("supplier"), "supplier_name": res.get("supplier_name"), "shipper": res.get("shipper"),
        "required": req, "gaps": gaps, "pages": res.get("pages") or [],
        "rules": [{"rule": t_["rule"], "kind": t_["kind"], "line": t_.get("line"), "field": t_.get("field"),
                   "before": t_.get("before"), "after": t_.get("after")} for t_ in res.get("trace") or []],
    }
