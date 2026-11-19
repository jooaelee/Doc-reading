"""확정한 정답(golden.json)과 AI 판독을 견줘 칸별, 공급사별, 건별 정확도를 잰다. AI 를 부르지 않는다.

정답 세트를 만든 뒤 무엇을 고치면 얼마나 나아졌는지 보는 자. 고치기 전에 한 번 재서 기준선으로 남기고,
고친 뒤 다시 읽고 또 재서 견준다.

  남긴 줄     정답 줄과 짝지은 AI 줄. 칸 정확도는 이 줄들의 칸으로 센다
  남는 줄     정답 어디에도 짝이 없는 AI 줄(사람이 지운 줄). 사람 손이 가는 일
  빠진 줄     AI 줄 어디에도 짝이 없는 정답 줄(사람이 더한 줄, 또는 다시 읽었더니 빠진 줄). 가장 나쁜 일
  칸          Main 7개(gerp_xlsx.ITEM_SPEC)와 통화. 대조 모드에서 사람이 본 칸만 센다. 다른 칸은 「빈칸(AI 가 비움)」, 「남는 값(정답은 빈칸)」, 「다른 값」 으로 나눔

🔴 정답은 그 판독의 줄 번호(_i)에 묶여 있다. 같은 판독이면 줄 번호로 짝짓고, 다시 읽어 줄이 바뀌었으면
   줄 내용(코드, 품명, 수량, 단가)으로 짝짓는다. 다시 읽은 판독은 같은 건(case)의 가장 새 작업에서 가져온다.

    python tools/accuracy.py                         # 재고 화면에 보임
    python tools/accuracy.py --save 기준선            # 재고 WUXI_HOME/accuracy/ 에 남김
    python tools/accuracy.py --compare 기준선         # 남긴 것과 견줌(이름 일부나 파일 경로)
    python tools/accuracy.py --cases PNS26081814 --detail   # 그 건의 틀린 칸과 남는 줄까지
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wuxi_paths  # noqa: E402
import wuxi_rules as R  # noqa: E402
from gerp_xlsx import ITEM_SPEC  # noqa: E402

ALL_COLS = [(tier, label, col, how) for tier, label, col, how, _w, _n in ITEM_SPEC]
MAIN = [c for c in ALL_COLS if c[0] == "Main"]
# 🔴 대조 모드는 Main 칸과 통화만 보여 준다. 다른 Sub 칸은 사람이 보지 않아 정답에 AI 값이 그대로 들어 있으므로
#    정확도가 아니다(늘 100%). --sub 로 보면 「전 판독과 같은지」만 뜻이 있다
CHECKED = MAIN + [c for c in ALL_COLS if c[2] == "Currency"]
COLS = CHECKED
# 다시 읽은 줄을 정답 줄과 짝지을 때 쓰는 칸과 무게. 정답이 빈 칸은 세지 않는다
MATCH_W = {"Material": 3, "Vendor Part No.": 3, "품명": 3, "Purchase Order": 2, "B/L quantity": 2, "Net Price": 2,
           "Invoice amount": 2, "Invoice No.": 1, "Serial No.": 2, "Item": 1}
MATCH_MIN = 5
HOW = {col: how for _t, _l, col, how in COLS}
HOW.update({"Invoice No.": "code", "Serial No.": "code", "Item": "code"})


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v):
    try:
        return float(_s(v).replace(",", ""))
    except ValueError:
        return None


def same(how: str, a, b) -> bool:
    a, b = _s(a), _s(b)
    if not a or not b:
        return not a and not b
    if how == "number":
        x, y = _num(a), _num(b)
        if x is not None and y is not None:
            return abs(x - y) <= 0.005 + 1e-9 * max(abs(x), abs(y))
    if how == "text":
        return R.norm_desc(a) == R.norm_desc(b)
    if how == "unit":
        return R.norm(a).rstrip(".") == R.norm(b).rstrip(".")
    return R.norm(a).replace(" ", "") == R.norm(b).replace(" ", "")


def _score(g: dict, a: dict) -> float:
    return sum(w for c, w in MATCH_W.items() if _s(g.get(c)) and same(HOW.get(c, "code"), g.get(c), a.get(c)))


def pair_rows(gold: list, read: list, same_read: bool) -> list:
    """(정답 줄 번호, AI 줄 번호 또는 None) 목록. same_read 면 정답의 _i 를 그대로 쓴다."""
    if same_read:
        return [(k, g["_i"] if isinstance(g.get("_i"), int) and 0 <= g["_i"] < len(read) else None)
                for k, g in enumerate(gold)]
    cand = sorted(((_score(g, a), -abs(k - i), k, i) for k, g in enumerate(gold) for i, a in enumerate(read)), reverse=True)
    gk, ai, out = set(), set(), {}
    for sc, _d, k, i in cand:
        if sc < MATCH_MIN:
            break
        if k in gk or i in ai:
            continue
        gk.add(k)
        ai.add(i)
        out[k] = i
    return [(k, out.get(k)) for k in range(len(gold))]


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load_sets(cases=None, all_jobs=False, jobs=None) -> list:
    """정답이 있는 건마다 (건, 공급사, 정답 줄, 판독 줄, 같은 판독인지, 판독 작업 id).

    jobs: 서버가 이미 들고 있는 작업 목록. 주면 job.json 을 다시 읽지 않고 golden.json 만 읽는다(운영 화면).
    """
    if jobs is None:
        jobs = []
        for d in sorted(glob.glob(os.path.join(wuxi_paths.work("jobs"), "*"))):
            j = _load(os.path.join(d, "job.json"))
            if not isinstance(j, dict):
                continue
            j["_dir"] = d
            jobs.append(j)
    else:
        jobs = [dict(j, _dir=os.path.join(wuxi_paths.work("jobs"), j["id"])) for j in jobs if isinstance(j, dict) and j.get("id")]
    latest = {}
    for j in jobs:
        if j.get("status") == "done" and (j.get("payload") or {}).get("items") is not None:
            k = j.get("case") or j["id"]
            if k not in latest or (j.get("created") or 0) > (latest[k].get("created") or 0):
                latest[k] = j
    out = []
    for j in jobs:
        if not all_jobs and j.get("source") != "bulk":      # 화면에서 올린 시험 묶음은 빼고 일괄 등록한 실제 건만
            continue
        g = _load(os.path.join(j["_dir"], "golden.json"))
        if not isinstance(g, dict):
            continue
        case = j.get("case") or j["id"][:8]
        if cases and case not in cases:
            continue
        rj = latest.get(j.get("case") or j["id"]) or j
        read = (rj.get("payload") or {}).get("items") or []
        # 같은 작업이라도 다시 읽었으면 줄 번호가 바뀐다. 다시 읽은 시각이 확정보다 늦으면 내용으로 짝짓는다
        reread = (rj.get("started") or 0) > (g.get("time") or 0)
        out.append({"case": case, "supplier": rj.get("supplier_name") or R.supplier_name(rj.get("supplier")),
                    "supplier_id": rj.get("supplier"), "gold": g.get("items") or [], "read": read,
                    "same_read": rj["id"] == j["id"] and not reread, "job": rj["id"], "confirmed": g.get("time"),
                    "checks": ((rj.get("payload") or {}).get("completeness") or {}).get("checks") or []})
    return out


def _blank():
    return {"ok": 0, "blank": 0, "extra": 0, "diff": 0}


def measure(sets: list, detail=False, sub=False) -> dict:
    COLS = ALL_COLS if sub else CHECKED      # noqa: N806
    cols = {col: _blank() for _t, _l, col, _h in COLS}
    rows = {"gold": 0, "read": 0, "kept": 0, "extra": 0, "missing": 0}
    by_sup, by_case, notes = {}, {}, []
    for s in sets:
        gold, read = s["gold"], s["read"]
        pairs = pair_rows(gold, read, s["same_read"])
        used = {i for _k, i in pairs if i is not None}
        c = {"gold": len(gold), "read": len(read), "kept": len(used), "extra": len(read) - len(used),
             "missing": sum(1 for _k, i in pairs if i is None), "main_ok": 0, "main_n": 0, "wrong": []}
        for k, i in pairs:
            if i is None:
                if detail:
                    c["wrong"].append(f"빠진 줄: {_s(gold[k].get('품명'))[:50]}")
                continue
            g, a = gold[k], read[i]
            for tier, label, col, how in COLS:
                gv, av = g.get(col), a.get(col)
                if same(how, gv, av):
                    kind = "ok"
                elif not _s(av):
                    kind = "blank"
                elif not _s(gv):
                    kind = "extra"
                else:
                    kind = "diff"
                cols[col][kind] += 1
                if tier == "Main":
                    c["main_n"] += 1
                    c["main_ok"] += kind == "ok"
                    if kind != "ok" and detail:
                        c["wrong"].append(f"줄 {k + 1} {label}: AI {_s(av)!r}, 정답 {_s(gv)!r}")
        if detail:
            for i, a in enumerate(read):
                if i not in used:
                    c["wrong"].append(f"남는 줄 {i + 1} ({_s(a.get('출처')) or 'CI'}): {_s(a.get('품명'))[:40]} "
                                      f"수량 {_s(a.get('B/L quantity'))}")
        for k in rows:
            rows[k] += c[k]
        sp = by_sup.setdefault(s["supplier"], {"cases": 0, "main_ok": 0, "main_n": 0, "extra": 0, "missing": 0, "gold": 0})
        sp["cases"] += 1
        for k in ("main_ok", "main_n", "extra", "missing", "gold"):
            sp[k] += c[k]
        by_case[s["case"]] = {k: v for k, v in c.items() if k != "wrong" or detail}
        if not s["same_read"]:
            notes.append(s["case"])
    main_ok = sum(cols[c[2]]["ok"] for c in MAIN)
    main_n = sum(sum(cols[c[2]].values()) for c in MAIN)
    return {"time": time.strftime("%Y-%m-%d %H:%M"), "cases": len(sets), "rows": rows, "cols": cols,
            "main": {"ok": main_ok, "n": main_n,
                     # 빠진 줄의 칸도 틀린 것으로 넣은 값. 줄을 빠뜨려 칸 정확도가 좋아 보이는 것을 막는다
                     "with_missing": main_ok / (main_n + rows["missing"] * len(MAIN)) if main_n else None},
            "suppliers": by_sup, "by_case": by_case, "matched_by_content": notes}


def pct(a, b) -> str:
    return f"{100 * a / b:5.1f}%" if b else "    -"


def show(m: dict, prev: dict | None = None) -> None:
    r, mn = m["rows"], m["main"]

    def dl(cur, old, more_is_better=True, fmt="{:+.1f}%p"):
        if prev is None or old is None or cur is None:
            return ""
        d = cur - old
        if abs(d) < 1e-9:
            return "  (같음)"
        good = (d > 0) == more_is_better
        return f"  ({fmt.format(d)} {'좋아짐' if good else '나빠짐'})"

    pm = (prev or {}).get("main") or {}
    pr = (prev or {}).get("rows") or {}
    print(f"정답 {m['cases']}건  ({m['time']})" + (f"  견준 것: {prev.get('time')} {prev.get('label', '')}" if prev else ""))
    print(f"  Main 칸 정확도     {pct(mn['ok'], mn['n'])}  ({mn['ok']}/{mn['n']}칸)"
          + dl(100 * mn['ok'] / mn['n'] if mn['n'] else None, 100 * pm['ok'] / pm['n'] if pm.get('n') else None))
    if mn["with_missing"] is not None:
        print(f"  빠진 줄까지 넣으면 {100 * mn['with_missing']:5.1f}%"
              + dl(100 * mn["with_missing"], 100 * pm["with_missing"] if pm.get("with_missing") is not None else None))
    print(f"  정답 줄 {r['gold']}, AI 줄 {r['read']}, 남는 줄 {r['extra']}"
          + dl(r['extra'], pr.get('extra'), False, "{:+d}줄") + f", 빠진 줄 {r['missing']}"
          + dl(r['missing'], pr.get('missing'), False, "{:+d}줄"))
    print("\n칸별 (맞음 / 빈칸 / 남는 값 / 다른 값)")
    rest = [c for c in ALL_COLS if c not in CHECKED]
    for tier, label, col, _h in CHECKED + rest:
        c = m["cols"].get(col)
        if c is None:
            continue
        if rest and col == rest[0][2]:
            print("  (아래 Sub 칸은 대조 모드에서 보지 않은 칸이라 정답이 AI 값 그대로입니다. 전 판독과 같은지만 뜻이 있습니다)")
        n = sum(c.values())
        old = ((prev or {}).get("cols") or {}).get(col)
        d = dl(100 * c["ok"] / n if n else None, 100 * old["ok"] / sum(old.values()) if old and sum(old.values()) else None)
        print(f"  {tier:4} {label:10} {pct(c['ok'], n)}  {c['ok']:4} / {c['blank']:3} / {c['extra']:3} / {c['diff']:3}{d}")
    print("\n공급사별 (Main 칸, 남는 줄, 빠진 줄)")
    for name, s in sorted(m["suppliers"].items(), key=lambda x: (x[1]["main_ok"] / x[1]["main_n"] if x[1]["main_n"] else 1,
                                                                 -x[1]["extra"])):
        if s["main_ok"] == s["main_n"] and not s["extra"] and not s["missing"]:
            continue
        print(f"  {name[:34]:34} {s['cases']:2}건  {pct(s['main_ok'], s['main_n'])}  남는 줄 {s['extra']:3}  빠진 줄 {s['missing']}")
    clean = sum(1 for s in m["suppliers"].values() if s["main_ok"] == s["main_n"] and not s["extra"] and not s["missing"])
    print(f"  (모두 맞은 공급사 {clean}곳은 뺌)")
    print("\n사람 손이 간 건")
    pc = (prev or {}).get("by_case") or {}
    for case, c in sorted(m["by_case"].items(), key=lambda x: -(x[1]["extra"] + x[1]["missing"] * 3 + x[1]["main_n"] - x[1]["main_ok"])):
        bad = c["main_n"] - c["main_ok"]
        if not (bad or c["extra"] or c["missing"]):
            continue
        o = pc.get(case)
        was = f"  (전: 틀린 칸 {o['main_n'] - o['main_ok']}, 남는 줄 {o['extra']}, 빠진 줄 {o['missing']})" if o else ""
        print(f"  {case:14} 틀린 칸 {bad:3}  남는 줄 {c['extra']:3}  빠진 줄 {c['missing']:2}  (정답 {c['gold']}줄, AI {c['read']}줄){was}")
        for w in c.get("wrong") or []:
            print(f"      {w}")
    if m["matched_by_content"]:
        print(f"\n다시 읽은 건 {len(m['matched_by_content'])}건은 줄 내용으로 짝지었습니다: {', '.join(m['matched_by_content'][:12])}")


def out_dir() -> str:
    return wuxi_paths.work("accuracy")


def find_saved(name: str) -> str | None:
    if os.path.isfile(name):
        return name
    fs = sorted(glob.glob(os.path.join(out_dir(), "*.json")))
    hit = [f for f in fs if name in os.path.basename(f)]
    return hit[-1] if hit else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--save", metavar="이름", help="잰 것을 WUXI_HOME/accuracy/ 에 남김")
    ap.add_argument("--compare", metavar="이름", help="남긴 것과 견줌(파일 이름 일부나 경로)")
    ap.add_argument("--cases", nargs="*", help="이 건들만")
    ap.add_argument("--detail", action="store_true", help="틀린 칸과 남는 줄까지")
    ap.add_argument("--sub", action="store_true", help="대조 모드에서 보지 않은 Sub 칸까지(전 판독과 같은지 보는 용도)")
    ap.add_argument("--all", action="store_true", help="화면에서 올린 시험 묶음까지")
    a = ap.parse_args()
    prev = None
    if a.compare:
        f = find_saved(a.compare)
        if not f:
            sys.exit(f"견줄 것을 찾지 못했습니다: {a.compare}")
        prev = _load(f)
    m = measure(load_sets(set(a.cases) if a.cases else None, all_jobs=a.all), detail=a.detail, sub=a.sub)
    if not m["cases"]:
        sys.exit("확정한 정답이 없습니다. 고객 화면 대조 모드에서 확정하면 생깁니다.")
    show(m, prev)
    if a.save:
        os.makedirs(out_dir(), exist_ok=True)
        m["label"] = a.save
        f = os.path.join(out_dir(), time.strftime("%Y%m%d-%H%M") + "-" + re.sub(r"[^\w가-힣-]+", "_", a.save) + ".json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(m, fh, ensure_ascii=False, indent=1)
        print(f"\n남김: {f}")


if __name__ == "__main__":
    main()
