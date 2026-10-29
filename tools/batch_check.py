"""실제 서류 묶음 여러 건을 차례로 읽어 빠짐 검사 결과를 모은다. 정답이 없어도 쓸 수 있는 근거(총액, 줄 수, 쪽)로 본다.

  python tools/batch_check.py work/docs_raw/hx --limit 10

건 폴더 하나가 묶음 하나다(폴더 안의 PDF 전부). 화면에는 숫자만 찍고(고객 자료를 남기지 않게) 자세한 결과는
work/batch/<시각>.json 에 둔다. 이미 읽은 건은 --resume 으로 건너뛴다.
"""
import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wuxi_engine  # noqa: E402
import wuxi_paths  # noqa: E402


def one(case_dir):
    pdfs = sorted(glob.glob(os.path.join(case_dir, "*.pdf")) + glob.glob(os.path.join(case_dir, "*.PDF")))
    t0 = time.perf_counter()
    try:
        res = wuxi_engine.read_bundle(pdfs, name=os.path.basename(case_dir))
    except Exception as e:
        return {"case": os.path.basename(case_dir), "error": wuxi_engine.friendly_error(str(e)),
                "sec": round(time.perf_counter() - t0, 1)}
    ex = res["ex"]
    n = {k: len([x for x in (ex.get(k) or []) if isinstance(x, dict)]) for k in ("ci_lines", "pl_lines", "cn_lines")}
    v = res["verify"]
    return {"case": os.path.basename(case_dir), "pdfs": len(pdfs), "kinds": res["kinds"], "n_scan": res["n_scan"],
            "lines": n, "invoices": len(ex.get("invoice_totals") or []),
            "completeness": res["completeness"], "fail": sum(1 for g, _, _ in v if g == "FAIL"),
            "warn": sum(1 for g, _, _ in v if g == "WARN"), "sec": round(time.perf_counter() - t0, 1),
            "calls": res.get("calls"), "ex": ex}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", help="앞서 남긴 결과 json. 거기 있는 건은 건너뛴다")
    a = ap.parse_args(argv)
    cases = sorted(d for d in glob.glob(os.path.join(a.root, "*")) if os.path.isdir(d))
    done = {}
    if a.resume and os.path.isfile(a.resume):
        done = {r["case"]: r for r in json.load(open(a.resume, encoding="utf-8"))}
    if a.limit:
        cases = cases[:a.limit]
    out_dir = wuxi_paths.work("batch")
    os.makedirs(out_dir, exist_ok=True)
    out = a.resume or os.path.join(out_dir, time.strftime("%Y%m%d-%H%M%S") + ".json")
    rows = list(done.values())
    for i, c in enumerate(cases, 1):
        if os.path.basename(c) in done:
            continue
        r = one(c)
        rows.append(r)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        comp = (r.get("completeness") or {}).get("verdict", "오류")
        ln = r.get("lines") or {}
        print(f"[{i}/{len(cases)}] 건{i:03d} {comp:7} 인보이스줄 {ln.get('ci_lines', '-')} 포장줄 {ln.get('pl_lines', '-')} "
              f"통관줄 {ln.get('cn_lines', '-')} 실패 {r.get('fail', '-')} {r.get('sec')}초", flush=True)
    ok = sum(1 for r in rows if (r.get("completeness") or {}).get("verdict") == "이상 없음")
    sus = sum(1 for r in rows if (r.get("completeness") or {}).get("verdict") == "빠진 줄 의심")
    print(f"모두 {len(rows)}건: 이상 없음 {ok}, 빠진 줄 의심 {sus}, 확인 못 함 또는 오류 {len(rows) - ok - sus}. 결과 {out}")


if __name__ == "__main__":
    main()
