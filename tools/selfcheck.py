"""설치 점검과 빠짐 시험: 지어낸 우시 묶음을 실제로 읽고(AI 호출), 정답표와 줄마다 칸마다 견주고, G-ERP 엑셀까지 만든다.

  python tools/make_sample.py [--lines 120 --invoices 3 --name long]
  python tools/selfcheck.py [--name long] [--scan]

가장 먼저 보는 것은 **빠진 줄이 없는가**다. 고객이 받는 G-ERP 품목 표는 서류의 품목 줄이 하나도 빠지면 안 된다.
화면(wuxi_web.py)과 같은 엔진(wuxi_engine.read_bundle)으로 돈다. 결과 엑셀은 work/samples/<이름>/out 에 남는다.
끝 코드: 0 모두 맞음, 1 틀린 칸이나 빠진 줄 있음.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gerp_xlsx  # noqa: E402
import wuxi_engine  # noqa: E402
import wuxi_paths  # noqa: E402

FIELDS = ("po_no", "po_item_no", "sap_code", "description", "qty", "unit", "unit_price", "amount", "country_of_origin")


def _same(k, a, b):
    if a in (None, "") or b in (None, ""):
        return a in (None, "") and b in (None, "")
    if k in ("qty", "unit_price", "amount"):
        try:
            return abs(float(str(a).replace(",", "")) - float(b)) < 0.005
        except (TypeError, ValueError):
            return False
    return " ".join(str(a).split()).upper() == " ".join(str(b).split()).upper()


def compare(ans_lines, got):
    """정답 줄마다 (인보이스 번호, PO 품목 번호)로 읽은 줄을 찾는다. 빠진 줄, 남는 줄, 틀린 칸을 센다."""
    pool = list(range(len(got)))
    missing, wrong, ok, total = [], [], 0, 0
    for want in ans_lines:
        hit = next((i for i in pool if _same("po_item_no", got[i].get("po_item_no"), want["po_item_no"])
                    and _same("invoice_no", got[i].get("invoice_no"), want["invoice_no"])), None)
        if hit is None:
            missing.append(f"{want['invoice_no']} {want['po_item_no']}")
            total += len(FIELDS)
            continue
        pool.remove(hit)
        for k in FIELDS:
            total += 1
            if _same(k, got[hit].get(k), want[k]):
                ok += 1
            else:
                wrong.append(f"{want['invoice_no']} {want['po_item_no']} {k}: 정답 {want[k]!r} / 읽음 {got[hit].get(k)!r}")
    return {"missing": missing, "extra": len(pool), "wrong": wrong, "ok": ok, "total": total}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", default=wuxi_paths.work("samples"))
    ap.add_argument("--name", default="sample")
    ap.add_argument("--scan", action="store_true", help="그림만 있는 묶음으로 OCR 길까지 본다")
    a = ap.parse_args(argv)
    base = os.path.join(a.samples, a.name)
    sub = "scan" if a.scan else "text"
    pdf = os.path.join(base, sub, f"{a.name}_bundle{'_scan' if a.scan else ''}.pdf")
    ans = json.load(open(os.path.join(base, "answer.json"), encoding="utf-8"))
    t0 = time.perf_counter()
    res = wuxi_engine.read_bundle([pdf], name=a.name)
    ex = res["ex"]
    got = [x for x in (ex.get("ci_lines") or []) if isinstance(x, dict)]
    print(f"쪽: {res['kinds']} (스캔 {res['n_scan']}쪽)   걸린 시간 {time.perf_counter() - t0:.0f}초   AI 호출 {res['calls']}회")
    c = compare(ans["lines"], got)
    print(f"품목 줄: 정답 {len(ans['lines'])}줄, 읽은 줄 {len(got)}줄, 빠진 줄 {len(c['missing'])}, 남는 줄 {c['extra']}")
    for m in c["missing"][:20]:
        print("  빠짐", m)
    for w in c["wrong"][:20]:
        print("  틀림", w)
    print(f"품목 칸 {c['ok']}/{c['total']} 맞음 ({100 * c['ok'] / max(c['total'], 1):.1f}%)")
    comp = res["completeness"]
    print(f"빠짐 검사(엔진): {comp['verdict']}  " + "; ".join(comp["notes"][:5]))
    rec = {"case": a.name, "ex": ex, "verify": res["verify"]}
    out_dir = os.path.join(base, "out")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"gerp_{sub}.xlsx")
    gerp_xlsx.write_xlsx([rec], out)
    items = gerp_xlsx.tables_for([rec]).get("items") or []
    print(f"G-ERP 품목 줄 {len(items)}줄 (정답 {len(ans['lines'])}줄)   엑셀: {out}")
    good = not c["missing"] and not c["extra"] and not c["wrong"] and len(items) == len(ans["lines"])
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
