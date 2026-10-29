# -*- coding: utf-8 -*-
"""추출 결과를 KEROL SO 업로드 양식(60열)으로 낸다. 사람이 확인 화면 대신 보는 표다.

    python3 tools/so_upload.py --pred work/out/batch --out work/out/so_upload.xlsx

시트가 둘이다.
  SO       KEROL 이 받는 60열 그대로. 코드를 못 찾은 칸(거래처, 장소)은 비워 둔다.
  검토     케이스마다 검증 FAIL 과 WARN, 채운 열 수, 사람이 봐야 할 자리. 업로드 전에 이 시트를 본다.

FAIL 이 하나라도 있는 행은 SO 시트에서 노란 바탕이다. 조용히 넘어가는 행이 없게 한다.
결과 파일에는 서류 값이 들어가므로 저장소 밖에 둔다.
"""
import sys

if sys.version_info < (3, 7):
    sys.exit("파이썬 3.7 이상이 필요합니다.")

import argparse, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import docpipe

REVIEW_COLS = ["케이스", "FAIL", "WARN", "채운 열", "FAIL 항목", "WARN 항목", "코드 미확정", "호출 초", "실패", "두 바퀴가 갈린 칸"]
# SO 행으로 가는 칸. 두 바퀴가 여기서 갈리면 사람이 본다.
SO_KEYS = ["hawb", "mawb", "invoice_no", "incoterms", "currency", "origin", "destination", "flight1",
           "customer_po", "item_desc", "ci_total", "pl_packages", "awb_pieces", "package_unit",
           "awb_gross", "pl_gross", "awb_chargeable", "shipper_name", "consignee_name"]


def load_preds(pred):
    out = []
    for fn in sorted(os.listdir(pred)):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        with open(os.path.join(pred, fn), encoding="utf-8") as f:
            rec = json.load(f)
        rec.setdefault("case", fn[:-5])
        out.append(rec)
    return out


def rows_for(rec):
    """(SO 행, 검토 행). 저장된 원출력을 같은 후처리로 다듬어 행을 만든다."""
    if rec.get("error") or not rec.get("raw"):
        return None, {"케이스": rec.get("case"), "실패": str(rec.get("error") or "빈 결과")[:120]}
    ex = docpipe.postprocess(rec["raw"])
    for k in ("shipper_code", "consignee_code", "pol_code", "pod_code"):
        if rec.get("codes"):
            field = k[:-5]
            d = rec["codes"].get(field) or {}
            if d.get("status") == "확정":
                ex[k] = d["code"]
    so = rec.get("so_row") or docpipe.to_so_row(ex)
    v = rec.get("verify") or []
    fails = [k for g, k, _ in v if g == "FAIL"]
    warns = [k for g, k, _ in v if g == "WARN"]
    unresolved = [f for f, d in (rec.get("codes") or {}).items() if d.get("status") != "확정"]
    review = {"케이스": rec.get("case"), "FAIL": len(fails), "WARN": len(warns),
              "채운 열": sum(1 for x in so.values() if x not in ("", None)),
              "FAIL 항목": ", ".join(fails), "WARN 항목": ", ".join(warns),
              "코드 미확정": ", ".join(unresolved),
              "호출 초": round(((rec.get("llm") or {}).get("wall") or 0) / 1000, 1), "실패": ""}
    return so, review


def differing(rec, other):
    """같은 케이스의 다른 바퀴 결과와 SO 칸을 견줘 갈린 칸 이름을 돌려준다."""
    from compare_prompts import flat
    if not other or not other.get("raw") or not rec.get("raw"):
        return []
    a, b = docpipe.postprocess(rec["raw"]), docpipe.postprocess(other["raw"])
    return [k for k in SO_KEYS if flat(a.get(k), k) != flat(b.get(k), k)]


def write_xlsx(recs, path, others=None):
    import openpyxl
    from openpyxl.styles import PatternFill, Font
    others = others or {}
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "SO"
    ws.append(docpipe.SO_COLS)
    rv = wb.create_sheet("검토")
    rv.append(REVIEW_COLS)
    yellow = PatternFill("solid", fgColor="FFF2CC")
    orange = PatternFill("solid", fgColor="F8CBAD")
    n_ok = n_flag = n_err = n_diff = 0
    for rec in recs:
        so, review = rows_for(rec)
        diff = differing(rec, others.get(rec.get("case")))
        review["두 바퀴가 갈린 칸"] = ", ".join(diff)
        rv.append([review.get(c, "") for c in REVIEW_COLS])
        if so is None:
            n_err += 1
            continue
        ws.append([so.get(c, "") for c in docpipe.SO_COLS])
        if review["FAIL"] or diff:
            n_flag += 1
            if diff: n_diff += 1
            for cell in ws[ws.max_row]:
                cell.fill = orange if diff else yellow
        else:
            n_ok += 1
    for sheet in (ws, rv):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
    wb.save(path)
    return {"rows": n_ok + n_flag, "clean": n_ok, "flagged": n_flag, "failed": n_err, "differ": n_diff}


def main(argv=None):
    ap = argparse.ArgumentParser(description="추출 결과를 SO 업로드 양식으로 낸다.")
    ap.add_argument("--pred", required=True, help="batch_run 이나 truth_score 가 남긴 결과 폴더")
    ap.add_argument("--out", required=True, help="xlsx 경로. 저장소 밖")
    ap.add_argument("--compare", help="다른 바퀴의 결과 폴더. SO 칸이 갈린 케이스를 검토 시트에 적는다")
    a = ap.parse_args(argv)
    recs = load_preds(os.path.abspath(os.path.expanduser(a.pred)))
    others = {r.get("case"): r for r in load_preds(os.path.abspath(os.path.expanduser(a.compare)))} if a.compare else {}
    s = write_xlsx(recs, os.path.abspath(os.path.expanduser(a.out)), others)
    print(f"SO 행 {s['rows']}건 (검증 통과이고 두 바퀴 같음 {s['clean']}, 봐야 할 것 {s['flagged']}, 그중 두 바퀴 갈림 {s['differ']}), "
          f"호출 실패 {s['failed']}건 → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
