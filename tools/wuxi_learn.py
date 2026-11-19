"""사람이 확인 화면에서 고친 것을 규칙 후보로 바꾼다. 후보는 운영 화면 「학습」에서 사람이 반영하거나 거절한다.

받는 신호 넷
  칸 수정      엑셀을 받거나 확정할 때 AI 값과 최종 값이 다른 칸
  줄 추가      AI 가 놓친 줄을 사람이 더함
  빠짐 판정    「빠진 줄 의심」을 사람이 원본과 대조해 「빠진 줄 없음」 또는 「빠진 줄 있음」으로 닫음
  확정         고객이 최종 값을 확정함. 정답 세트가 되어 후보 미리보기에 쓰인다

만드는 후보
  값 바꾸기    단위나 원산지의 표기만 바뀐 경우(PCS. → PCS)
  코드 사전    SAP 코드, 자재 코드, HIOS, 부품 번호, 원산지, HS, 단위를 품명이나 부품 번호에 묶어 채움
  열 이름      비어 있던 코드 칸을 사람이 채웠고, 그 값이 원본 표의 어떤 열 아래에 있는지 찾은 경우
  판독 지침    사람이 줄을 더한 경우(긴 표를 쪽마다 끝까지 읽게 함)
  빠짐 검사    「빠진 줄 의심」을 사람이 「빠진 줄 없음」으로 닫은 경우(그 공급사에서 해당 검사를 끔)

🔴 학습 건수는 많지 않다(우시는 하루 몇 묶음). 그래서 같은 수정이 한 번만 있어도 후보를 만들고, 근거 건수와 미리보기를
   보여 준 뒤 사람이 반영을 정한다. 저절로 반영하지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wuxi_paths  # noqa: E402
import wuxi_rules as R  # noqa: E402

# 표의 열 → 판독 칸
COL_FIELD = {
    "Sap Code": "sap_code", "Material Code": "material_code", "HIOS No.": "hios_no", "Part No.": "part_no",
    "원산지": "country_of_origin", "HS": "hs_code", "단위": "unit", "Purchase Order": "po_no", "Item": "po_item_no",
    "Po Item No.": "po_item_no", "SK Hynix I/V No.": "iv_no", "Serial No.": "serial_no", "품명": "description",
    "B/L quantity": "qty", "Invoice amount": "amount", "Net Price": "unit_price", "Case No.": "case_no",
    "순중량(kg)": "net", "총중량(kg)": "gross",
}
FIELD_COL = {"sap_code": "Material", "material_code": "Material", "hios_no": "Vendor Part No.", "part_no": "Vendor Part No.",
             "country_of_origin": "원산지", "hs_code": "HS", "unit": "단위"}
EDIT_KINDS = ("code", "alias", "header", "guide")     # record_edits 가 만드는 후보. 빠짐 검사(check)는 review 가 만든다
HEADER_LEARN = ("po_no", "po_item_no", "sap_code", "material_code", "hios_no", "part_no", "iv_no", "serial_no")
MISSING_GUIDE = ("The invoice and packing-list tables of this supplier continue across page breaks and repeat the column "
                 "header on each page. Read every printed line on every page through to the last page, including the lines "
                 "right after a page break, and never stop at the first page.")
MISSING_LABEL = "인보이스와 포장명세서 표가 쪽을 넘겨 이어지므로, 마지막 쪽까지 줄을 빠짐없이 읽게 합니다"


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _log(row: dict) -> None:
    f = wuxi_paths.work("feedback.jsonl")
    os.makedirs(os.path.dirname(f), exist_ok=True)
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(), **row}
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def feedback(days=30) -> list:
    cut = time.time() - days * 86400
    out = []
    try:
        with open(wuxi_paths.work("feedback.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if (r.get("ts") or 0) >= cut:
                    out.append(r)
    except OSError:
        pass
    return out


def field_for(col: str, row: dict) -> str | None:
    """화면의 열이 어떤 판독 칸인지. Material 과 Vendor Part No. 는 원래 값이 어디서 왔는지로 고른다."""
    if col == "Material":
        # 엑셀 쪽이 하이닉스 자재 번호를 고른 칸(gerp_xlsx.hynix_material). 예전 줄에는 없다
        if row.get("_mat_key") in ("sap_code", "material_code", "part_no", "hios_no"):
            return row["_mat_key"]
        if _s(row.get("Sap Code")):
            return "sap_code"
        return "material_code" if _s(row.get("Material Code")) else "sap_code"
    if col == "Vendor Part No.":
        return "hios_no" if _s(row.get("HIOS No.")) else "part_no"
    return COL_FIELD.get(col)


def _part(row: dict) -> str:
    return _s(row.get("Vendor Part No.")) or _s(row.get("Part No.")) or _s(row.get("HIOS No."))


# ───────────────────────────────────────── 원본 표에서 열 이름 찾기
def _sig(v: str) -> str:
    return re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "9", v.strip()))


# 머리글이 아닌 줄. 「PO NO :4302056146」 처럼 이름과 값이 한 줄에 찍힌 줄이 값 위에 있으면 그 값은 표의 열이 아니라
# 머리 칸 묶음에 있다(열 이름 후보를 만들지 않음). 다섯 자리 넘는 숫자가 든 줄은 다른 품목의 줄이라 건너뛰고 더 올라간다
_LABEL_VALUE = re.compile(r"[:：]\s*\S*\d")
_LONG_NUM = re.compile(r"\d{5,}")


def find_header(pdfs, value: str) -> str | None:
    """값이 원본 표의 어느 열 아래에 있는지 찾아 그 열의 머리글을 돌려준다.

    값 위쪽으로 같은 세로줄을 거슬러 올라가며, 값과 꼴이 같은 줄(다른 품목의 같은 칸)은 건너뛰고
    처음 나오는 글자 줄을 머리글로 본다.
    """
    v = _s(value)
    if len(v) < 3:
        return None
    try:
        import pymupdf
    except ImportError:
        return None
    for path in pdfs or []:
        try:
            doc = pymupdf.open(path)
        except Exception:
            continue
        try:
            for page in list(doc)[:20]:
                rects = page.search_for(v)
                if not rects:
                    continue
                r = rects[0]
                words = page.get_text("words")
                x0, x1 = r.x0 - 4, r.x1 + 4
                above = [w for w in words if w[3] <= r.y0 + 1 and w[1] >= r.y0 - 420]
                rows = {}
                for w in above:
                    rows.setdefault(round(w[1] / 3), []).append(w)
                line_of = {}
                for w in words:
                    line_of.setdefault(round(w[1] / 3), []).append(w)
                for key in sorted(rows, reverse=True):
                    ws = sorted((w for w in rows[key] if w[2] >= x0 and w[0] <= x1), key=lambda w: w[0])
                    if not ws:
                        continue
                    # 건너뛰는 줄(다른 품목의 같은 칸)이 값보다 넓으면 열도 그만큼 넓다. 짧은 값이 왼쪽에 붙고
                    # 머리글이 가운데 찍힌 표(FLUSA4221 의 BISV 와 ITEM CODE)에서 머리글을 놓치지 않게 넓혀 둔다.
                    # 낱말 하나일 때만 넓힌다. 칸 사이가 좁아 옆 칸 낱말이 걸리면 열이 옆 칸까지 번진다
                    span = (min(w[0] for w in ws) - 4, max(w[2] for w in ws) + 4) if len(ws) == 1 else (x0, x1)
                    # 머리글은 값보다 넓은 일이 많다. 같은 줄에서 띄어쓰기 한 칸 안쪽으로 붙은 낱말을 양옆으로 더 잇는다
                    full = sorted(line_of.get(key, ws), key=lambda w: w[0])
                    i0, i1 = full.index(ws[0]), full.index(ws[-1])
                    while i0 > 0 and ws[0][0] - full[i0 - 1][2] <= 6:
                        i0 -= 1
                        ws = [full[i0]] + ws
                    while i1 < len(full) - 1 and full[i1 + 1][0] - ws[-1][2] <= 6:
                        i1 += 1
                        ws = ws + [full[i1]]
                    text = " ".join(w[4] for w in ws).strip()
                    if (not text or text == v or _sig(text) == _sig(v) or not re.search(r"[A-Za-z一-鿿가-힣]", text)
                            or re.fullmatch(r"[\d.,\-/ ]+", text)):
                        x0, x1 = min(x0, span[0]), max(x1, span[1])
                        continue
                    if _LABEL_VALUE.search(text):
                        return None
                    if _LONG_NUM.search(text):
                        x0, x1 = min(x0, span[0]), max(x1, span[1])
                        continue
                    if len(text) > 40:
                        return None
                    return text
        finally:
            doc.close()
    return None


# ───────────────────────────────────────── 신호 받기
# 앱이 채우는 열(BL No. 는 파일 이름). 판독 값이 아니므로 바뀌어도 학습 신호로 보지 않는다
NOT_LEARNED = {"BL No."}


def diff(job: dict, final_items: list, deleted: list | None = None) -> dict:
    """AI 값(payload.items)과 최종 값의 차이. 최종 줄의 _i 가 원래 줄 번호이고, _i 가 없는 줄은 사람이 더한 줄이다."""
    orig = (job.get("payload") or {}).get("items") or []
    cols = [c for c in ((job.get("payload") or {}).get("cols") or {}).get("gerp", []) +
            ((job.get("payload") or {}).get("cols") or {}).get("extra", [])] or list(COL_FIELD)
    cells, added, seen = [], [], set()
    for k, row in enumerate(final_items or []):
        if not isinstance(row, dict):
            continue
        i = row.get("_i")
        if not isinstance(i, int) or not (0 <= i < len(orig)):
            if any(_s(row.get(c)) for c in cols if c not in NOT_LEARNED):
                added.append(k)
            continue
        seen.add(i)
        o = orig[i]
        for c in cols:
            if c.startswith("_") or c not in row or c in NOT_LEARNED:
                continue
            b, a = _s(o.get(c)), _s(row.get(c))
            if b != a:
                cells.append({"row": i, "col": c, "before": b, "after": a, "desc": _s(o.get("품명")),
                              "invoice": _s(o.get("Invoice No.")), "part": _part(o), "orig": o})
    gone = sorted(set(deleted or []) | ({i for i in range(len(orig))} - seen if final_items is not None else set()))
    return {"cells": cells, "added": added, "deleted": [i for i in gone if isinstance(i, int)]}


def record_edits(job: dict, final_items: list, deleted=None, by="", kind="xlsx") -> dict:
    """엑셀을 받거나 확정할 때 부른다. 바뀐 칸을 기록하고 규칙 후보를 만든다."""
    d = diff(job, final_items, deleted)
    sid = job.get("supplier") or R.COMMON
    jid = job.get("id", "")[:12]
    made, todo = [], []
    for c in d["cells"]:
        _log({"type": "cell", "job": jid, "supplier": sid, "col": c["col"], "before": c["before"], "after": c["after"],
              "desc": c["desc"][:80], "via": kind})
        ev = {"job": jid, "time": time.time(), "row": c["row"], "col": c["col"], "before": c["before"],
              "after": c["after"], "desc": c["desc"][:120], "invoice": c["invoice"], "supplier": sid}
        todo += [(kind_, data, ev) for kind_, data in suggest(c, job)]
    # 같은 작업을 다시 확정하면 차이를 AI 값부터 새로 센다. 그 전 확정에서 이 작업이 남긴 후보 근거는 걷어 내고,
    # 이번에 다시 나오지 않는 후보는 근거가 없으면 지운다
    again = [(k, sid, R.key_of(k, dt)) for k, dt, _ in todo]
    if d["added"]:
        again.append(("guide", sid, R.key_of("guide", {"text": MISSING_GUIDE})))
    R.drop_job_evidence(jid, EDIT_KINDS, again=again)
    for kind_, data, ev in todo:
        r = R.upsert_candidate(kind_, sid, data, ev)
        if r:
            made.append(r["id"])
    if d["added"]:
        _log({"type": "added", "job": jid, "supplier": sid, "n": len(d["added"]), "via": kind})
        r = R.upsert_candidate("guide", sid, {"text": MISSING_GUIDE, "label": MISSING_LABEL},
                               {"job": jid, "time": time.time(), "row": -1, "col": "", "before": "",
                                "after": f"사람이 줄 {len(d['added'])}개를 더함", "supplier": sid})
        if r:
            made.append(r["id"])
    if d["deleted"]:
        _log({"type": "deleted", "job": jid, "supplier": sid, "n": len(d["deleted"]), "via": kind})
    summary = {"cells": len(d["cells"]), "added": len(d["added"]), "deleted": len(d["deleted"]),
               "candidates": sorted(set(made)), "time": time.time(), "via": kind}
    job["edits"] = {**summary, "detail": [{k: v for k, v in c.items() if k != "orig"} for c in d["cells"][:300]]}
    return summary


# 그 묶음에만 있는 품명. 수량(6EA), 슬롯 번호(#24), 날짜(02/12/2026)가 든 품명은 다음 묶음에서 같은 글자로 오지 않는다
_ONE_OFF_DESC = re.compile(r"\d\s*EA\b|#\s*\d|\b\d{1,2}/\d{1,2}/\d{2,4}\b", re.I)


def one_off_desc(desc: str) -> bool:
    return bool(_ONE_OFF_DESC.search(_s(desc)))


def suggest(c: dict, job: dict) -> list:
    """칸 수정 하나에서 나올 수 있는 후보(종류, 값) 목록."""
    f = field_for(c["col"], c["orig"])
    b, a = c["before"], c["after"]
    out = []
    if not f or not a:
        return out
    if f in R.ALIAS_FIELDS and b and R.norm(b) != R.norm(a) and len(b) <= 30 and len(a) <= 30:
        out.append(("alias", {"field": f, "from": b, "to": a}))
        return out
    if f in HEADER_LEARN and not b:
        h = find_header(job.get("pdfs") or [], a)
        builtin = {R.norm(x) for x in R.BUILTIN_HEADERS.get(f, [])}
        if h and R.norm(h) not in builtin:
            out.append(("header", {"field": f, "header": h}))
    if f in R.CODE_FIELDS:
        part = c.get("part")
        if part and f not in ("hios_no", "part_no"):
            out.append(("code", {"on": "part", "key": part, "field": f, "value": a, "mode": "fill" if not b else "replace"}))
        elif c.get("desc") and not one_off_desc(c["desc"]):
            out.append(("code", {"on": "desc", "key": c["desc"], "match": "exact", "field": f, "value": a,
                                 "mode": "fill" if not b else "replace"}))
    return out


def review(job: dict, result: str, by="") -> dict:
    """「빠진 줄 의심」을 사람이 원본과 대조한 결과. ok 는 빠진 줄 없음, missing 은 빠진 줄 있음."""
    comp = (job.get("payload") or {}).get("completeness") or {}
    sid = job.get("supplier") or R.COMMON
    jid = job.get("id", "")[:12]
    bad = [c for c in comp.get("checks") or [] if c.get("state") == "bad"]
    _log({"type": "review", "job": jid, "supplier": sid, "result": result, "checks": [c["check"] for c in bad]})
    made = []
    if result == "ok":
        for c in bad:
            r = R.upsert_candidate("check", sid, {"check": c["check"], "enabled": False},
                                   {"job": jid, "time": time.time(), "row": -1, "col": c["name"], "before": c["note"],
                                    "after": "사람이 원본과 대조해 빠진 줄 없음으로 닫음", "supplier": sid})
            if r:
                made.append(r["id"])
    job["review"] = {"result": result, "time": time.time(), "by": by, "checks": [c["check"] for c in bad]}
    return {"result": result, "candidates": made}


def confirm(job: dict, final_items: list, by="") -> dict:
    """고객이 최종 값을 확정했다. 정답 세트가 된다."""
    summary = record_edits(job, final_items, by=by, kind="confirm")
    d = os.path.join(wuxi_paths.work("jobs"), job["id"])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "golden.json"), "w", encoding="utf-8") as fh:
        json.dump({"time": time.time(), "items": final_items}, fh, ensure_ascii=False)
    job["confirmed"] = time.time()
    _log({"type": "confirm", "job": job["id"][:12], "supplier": job.get("supplier") or R.COMMON,
          "cells": summary["cells"], "added": summary["added"]})
    return summary


def save_final(job: dict, final_items: list) -> None:
    d = os.path.join(wuxi_paths.work("jobs"), job["id"])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "final.json"), "w", encoding="utf-8") as fh:
        json.dump({"time": time.time(), "items": final_items}, fh, ensure_ascii=False)


# ───────────────────────────────────────── 미리보기, 격상 제안
def _finals(jobs: list) -> list:
    out = []
    for job in jobs:
        d = os.path.join(wuxi_paths.work("jobs"), job["id"])
        for name, golden in (("golden.json", True), ("final.json", False)):
            try:
                with open(os.path.join(d, name), encoding="utf-8") as fh:
                    out.append((job, json.load(fh).get("items") or [], golden))
                    break
            except (OSError, ValueError):
                continue
    return out


def preview(rule: dict, jobs: list) -> dict:
    """규칙을 지난 작업에 적용했을 때 바뀌는 칸과, 그 가운데 사람이 확정한 값과 맞는 칸을 센다.

    코드 사전과 값 바꾸기만 AI 를 다시 부르지 않고 계산할 수 있다. 열 이름과 판독 지침은 다시 읽어야 효과가 보인다.
    """
    kind, d = rule["kind"], rule["data"]
    scope = rule["scope"]
    if kind not in ("code", "alias"):
        why = {"header": "열 이름은 판독할 때 쓰이므로, 반영한 뒤 그 공급사 서류를 「다시 읽기」로 확인합니다.",
               "guide": "판독 지침은 판독할 때 쓰이므로, 반영한 뒤 그 공급사 서류를 「다시 읽기」로 확인합니다.",
               "check": "빠짐 검사를 켜고 끈 것은 1분 안에 확정하지 않은 작업의 판정에도 다시 겁니다. 금액 허용 차이는 다음 판독부터 쓰입니다.",
               "required": "필수 칸 설정은 다음 판독부터 확인 화면 표시에 쓰입니다."}[kind]
        return {"computable": False, "note": why}
    target = FIELD_COL.get(d["field"])
    jobs_ = [j for j in jobs if scope == R.COMMON or j.get("supplier") == scope]
    res = {"computable": True, "jobs": 0, "rows": 0, "changed": 0, "good": 0, "bad": 0, "unknown": 0, "examples": []}
    finals = {j["id"]: (items, golden) for j, items, golden in _finals(jobs_)}
    for job in jobs_:
        items = (job.get("payload") or {}).get("items") or []
        if not items:
            continue
        res["jobs"] += 1
        fin, golden = finals.get(job["id"], (None, False))
        by_i = {r.get("_i"): r for r in fin or [] if isinstance(r, dict) and isinstance(r.get("_i"), int)}
        for i, row in enumerate(items):
            cur = _s(row.get(target))
            if kind == "alias":
                if R.norm(cur) != R.norm(d["from"]):
                    continue
                new = d["to"]
            else:
                key = R.norm_desc(d["key"]) if d["on"] == "desc" else R.norm(d["key"])
                have = R.norm_desc(row.get("품명")) if d["on"] == "desc" else R.norm(_part(row))
                hit = bool(key and have) and ((key in have) if d.get("match") == "contains" else have == key)
                if not hit:
                    continue
                res["rows"] += 1
                if d.get("mode") != "replace" and cur:
                    continue
                new = d["value"]
            if R.norm(cur) == R.norm(new):
                continue
            res["changed"] += 1
            want = by_i.get(i)
            if want is None:
                res["unknown"] += 1
                ok = None
            else:
                ok = R.norm(want.get(target)) == R.norm(new)
                res["good" if ok else "bad"] += 1
            if len(res["examples"]) < 6:
                res["examples"].append({"job": job["id"][:8], "row": i + 1, "desc": _s(row.get("품명"))[:60],
                                        "before": cur, "after": new, "ok": ok, "golden": golden})
    return res


def promotions() -> list:
    """공급사 규칙 가운데 다른 공급사에서도 같은 내용이 나온 것. 공통으로 올리자고 제안한다."""
    rules = R.all_rules()
    out = []
    for r in rules:
        if r["status"] != "active" or r["scope"] == R.COMMON or r["kind"] in ("check", "required"):
            continue
        k = R.key_of(r["kind"], r["data"])
        others = {x["scope"] for x in rules if x["id"] != r["id"] and x["kind"] == r["kind"] and x["scope"] not in (R.COMMON, r["scope"])
                  and x["status"] in ("active", "candidate") and R.key_of(x["kind"], x["data"]) == k}
        others |= {e.get("supplier") for e in r.get("evidence") or [] if e.get("supplier") not in (None, R.COMMON, r["scope"])}
        if others:
            out.append({"rule": r["id"], "text": R.describe(r), "scope": r["scope"], "scope_name": R.supplier_name(r["scope"]),
                        "others": sorted(R.supplier_name(o) for o in others), "applied": (r.get("stats") or {}).get("applied", 0)})
    return out
