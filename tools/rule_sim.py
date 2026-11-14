"""규칙을 반영하거나 공통으로 격상하기 전에, 확정한 정답 세트에 미리 적용해 정확도가 어떻게 바뀌는지 본다.

AI 를 다시 부르지 않는다. 저장된 판독 결과(G-ERP 줄)에 규칙을 덧입혀 tools/accuracy.py 와 같은 자로 잰다.

  코드 사전, 값 바꾸기   Main 칸 정확도 전과 후, 바로잡는 칸, 틀어지는 칸, 걸리는 건
  빠짐 검사            「빠진 줄 의심」이 「이상 없음」으로 바뀌는 건과, 그중 사람이 실제로 줄을 더한 건(놓칠 건)
  열 이름, 판독 지침     AI 가 읽는 방식을 바꾸므로 계산할 수 없다. 반영한 뒤 그 공급사 정답 건을 다시 읽어 견준다

🔴 저장된 판독에는 이미 반영된 규칙이 들어 있다. 그래서 여기서 재는 것은 「지금 규칙에 이 규칙 하나를 더하면」이다.
🔴 줄 짝은 규칙을 덧입히기 전 판독으로 한 번만 짓는다. 덧입힌 값 때문에 짝이 바뀌어 숫자가 흔들리지 않게 한다.

    python tools/rule_sim.py                 # 지금 후보 모두
    python tools/rule_sim.py --rule <id>     # 규칙 하나(공통으로 반영했을 때도 함께)
"""
from __future__ import annotations

import argparse
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accuracy as A  # noqa: E402
import wuxi_rules as R  # noqa: E402
from wuxi_learn import FIELD_COL, _part  # noqa: E402

NOT_COMPUTABLE = {
    "header": "열 이름은 AI 가 읽는 방식을 바꾸므로 미리 잴 수 없습니다. 반영한 뒤 이 공급사 정답 건을 「다시 읽기」하면 견줄 수 있습니다.",
    "guide": "판독 지침은 AI 가 읽는 방식을 바꾸므로 미리 잴 수 없습니다. 반영한 뒤 이 공급사 정답 건을 「다시 읽기」하면 견줄 수 있습니다.",
    "required": "필수 칸 설정은 화면 표시만 바꾸므로 정확도와 상관이 없습니다.",
}


def in_scope(s: dict, scope: str, skip: str | None = None) -> bool:
    if skip and s.get("supplier_id") == skip:
        return False
    return scope == R.COMMON or s.get("supplier_id") == scope


def apply_rows(rule: dict, rows: list) -> list:
    """판독 줄(G-ERP 줄)에 규칙 하나를 덧입힌 사본과, 바뀐 자리 [(줄 번호, 열, 전, 후)]."""
    kind, d = rule["kind"], rule.get("data") or {}
    col = FIELD_COL.get(d.get("field"))
    out, changed = copy.deepcopy(rows), []
    if not col:
        return out, changed
    for i, row in enumerate(out):
        cur = A._s(row.get(col))
        if kind == "alias":
            if not cur or R.norm(cur) != R.norm(d.get("from")):
                continue
            new = d.get("to")
        else:
            if A._s(row.get("출처")) == "PL":           # 코드 사전은 인보이스 줄에만 쓰인다(wuxi_rules.apply_post)
                continue
            on_desc = d.get("on") == "desc"
            key = R.norm_desc(d.get("key")) if on_desc else R.norm(d.get("key"))
            have = R.norm_desc(row.get("품명")) if on_desc else R.norm(_part(row))
            hit = bool(key and have) and ((key in have) if d.get("match") == "contains" else have == key)
            if not hit or (d.get("mode") != "replace" and cur):
                continue
            new = d.get("value")
        if R.norm(cur) == R.norm(new):
            continue
        row[col] = new
        changed.append((i, col, cur, A._s(new)))
    return out, changed


def _pairs(s: dict) -> list:
    """정답 줄과 판독 줄의 짝. 후보마다 다시 짓지 않게 세트에 담아 둔다."""
    if "_pairs" not in s:
        s["_pairs"] = A.pair_rows(s["gold"], s["read"], s["same_read"])
    return s["_pairs"]


def _main_ok(gold: dict, row: dict) -> int:
    return sum(A.same(how, gold.get(col), row.get(col)) for _t, _l, col, how in A.MAIN)


def _cells(plan: list, sets: list) -> dict:
    """plan: [(규칙, 범위, 건너뛸 공급사)]. 범위에 드는 정답 건의 판독에 차례로 덧입혀 Main 칸을 전과 후로 잰다.

    fixed 는 틀린 칸이 맞게 바뀐 칸, broken 은 맞던 칸이 틀어진 칸, wrong 은 틀린 칸이 다른 틀린 값으로 바뀐 칸
    (대개 빈 칸을 정답과 다른 값으로 채움). 정확도 숫자는 그대로여도 wrong 이 있으면 그 규칙은 의심스럽다.
    """
    res = {"computable": True, "kind": "cells", "cases": len(sets), "scopeCases": 0, "touched": 0,
           "ok0": 0, "ok1": 0, "n": 0, "fixed": 0, "broken": 0, "wrong": 0, "changed": 0, "unpaired": 0,
           "examples": [], "byCase": {}, "suppliers": {}}
    for s in sets:
        gold, read = s["gold"], s["read"]
        pairs = _pairs(s)
        after, changed, hit = read, [], False
        for rule, scope, skip in plan:
            if in_scope(s, scope, skip):
                hit = True
                after, ch = apply_rows(rule, after)
                changed += ch
        res["scopeCases"] += hit
        sp = res["suppliers"].setdefault(s["supplier"], {"cases": 0, "ok0": 0, "ok1": 0, "n": 0, "scope": False})
        sp["cases"] += 1
        sp["scope"] = sp["scope"] or hit
        g_of = {i: k for k, i in pairs if i is not None}
        for k, i in pairs:
            if i is None:
                continue
            o0, o1 = _main_ok(gold[k], read[i]), _main_ok(gold[k], after[i])
            for d in (res, sp):
                d["n"] += len(A.MAIN)
                d["ok0"] += o0
                d["ok1"] += o1
        if not changed:
            continue
        res["touched"] += 1
        fx = bk = wr = 0
        for i, col, was, now in changed:
            res["changed"] += 1
            k = g_of.get(i)
            if k is None:                                   # 정답에 짝이 없는 줄(사람이 지운 줄)
                res["unpaired"] += 1
                ok = None
            else:
                how = A.HOW.get(col, "code")
                b0, b1 = A.same(how, gold[k].get(col), was), A.same(how, gold[k].get(col), now)
                fx += (not b0) and b1
                bk += b0 and not b1
                wr += (not b0) and not b1
                ok = b1
            if len(res["examples"]) < 8:
                res["examples"].append({"case": s["case"], "row": i + 1, "col": col, "before": was, "after": now,
                                        "gold": "" if k is None else A._s(gold[k].get(col)), "ok": ok})
        res["fixed"] += fx
        res["broken"] += bk
        res["wrong"] += wr
        res["byCase"][s["case"]] = {"fixed": fx, "broken": bk, "wrong": wr, "changed": len(changed)}
    res["before"] = res["ok0"] / res["n"] if res["n"] else None
    res["after"] = res["ok1"] / res["n"] if res["n"] else None
    # 걸린 공급사만 남긴다(정확도가 바뀐 곳). 범위 공급사는 걸리지 않았어도 남겨 「정답 몇 건」 을 보인다
    res["suppliers"] = [{"name": k, "cases": v["cases"], "before": v["ok0"] / v["n"] if v["n"] else None,
                         "after": v["ok1"] / v["n"] if v["n"] else None}
                        for k, v in res["suppliers"].items() if v["ok0"] != v["ok1"] or (v["scope"] and len(plan) == 1
                                                                                          and plan[0][1] != R.COMMON)]
    return res


def _checks(rule: dict, sets: list, scope: str, skip=None) -> dict:
    """빠짐 검사를 끄면 「빠진 줄 의심」에서 풀리는 건. 정답에 사람이 더한 줄(짝 없는 정답 줄)이 있으면 놓칠 건이다."""
    d = rule.get("data") or {}
    name = R.CHECKS.get(d.get("check"), (d.get("check"),))[0]
    res = {"computable": True, "kind": "check", "check": name, "cases": len(sets), "scopeCases": 0, "flagged": 0,
           "cleared": 0, "missed": 0, "examples": []}
    if d.get("enabled", True):
        res["note"] = "검사를 켜는 규칙이라 풀리는 건이 없습니다."
        return res
    for s in sets:
        if not in_scope(s, scope, skip):
            continue
        res["scopeCases"] += 1
        bad = [c.get("check") for c in s.get("checks") or [] if c.get("state") == "bad"]
        if d.get("check") not in bad:
            continue
        res["flagged"] += 1
        if any(b != d.get("check") for b in bad):
            continue                                        # 다른 검사도 어긋나 여전히 「빠진 줄 의심」
        res["cleared"] += 1
        missing = sum(1 for _k, i in _pairs(s) if i is None)
        res["missed"] += missing > 0
        if len(res["examples"]) < 8:
            res["examples"].append({"case": s["case"], "missing": missing})
    return res


def simulate(rule: dict, sets: list, scope: str | None = None, skip: str | None = None) -> dict:
    """rule 을 scope(기본은 규칙의 범위)로 반영했을 때. skip: 이미 그 규칙이 쓰이는 공급사(격상할 때)."""
    scope = scope or rule.get("scope") or R.COMMON
    kind = rule.get("kind")
    if kind in ("code", "alias"):
        if not FIELD_COL.get((rule.get("data") or {}).get("field")):
            return {"computable": False, "note": "이 칸은 대조 모드에서 보지 않는 칸이라 정답으로 잴 수 없습니다."}
        res = _cells([(rule, scope, skip)], sets)
    elif kind == "check":
        res = _checks(rule, sets, scope, skip)
    else:
        n = sum(1 for s in sets if in_scope(s, scope, skip))
        return {"computable": False, "note": NOT_COMPUTABLE.get(kind, "미리 잴 수 없는 규칙입니다."), "scopeCases": n,
                "cases": len(sets)}
    res["scope"] = scope
    res["scopeName"] = R.supplier_name(scope)
    return res


def simulate_all(rules: list, sets: list) -> dict:
    """후보 여럿(코드 사전, 값 바꾸기)을 한꺼번에 반영했을 때. 미리 잴 수 없는 후보는 개수만 센다."""
    ok = [r for r in rules if r.get("kind") in ("code", "alias") and FIELD_COL.get((r.get("data") or {}).get("field"))]
    res = _cells([(r, r.get("scope") or R.COMMON, None) for r in ok], sets)
    res.update(rules=len(ok), skipped=len(rules) - len(ok))
    return res


def pct(v) -> str:
    return "-" if v is None else f"{100 * v:.1f}%"


def text(res: dict) -> str:
    """화면과 명령줄에 쓰는 한 줄 요약(합쇼체)."""
    if not res.get("computable"):
        return res.get("note") or ""
    if res["kind"] == "check":
        if res.get("note"):
            return res["note"]
        if not res["cleared"]:
            return f"정답 {res['scopeCases']}건 가운데 이 검사만 어긋나 풀리는 건이 없습니다."
        warn = f" 그중 {res['missed']}건은 사람이 줄을 더한 건이라, 끄면 빠진 줄을 놓칩니다." if res["missed"] else " 그중 실제로 줄이 빠졌던 건은 없습니다."
        return f"정답 {res['scopeCases']}건 가운데 {res['cleared']}건이 「이상 없음」으로 바뀝니다.{warn}"
    if not res["touched"]:
        return f"정답 {res['cases']}건(이 범위 {res['scopeCases']}건)에는 이 규칙이 걸리는 칸이 없어 정확도가 그대로입니다."
    sups = "".join(f" {s['name'][:24]}({s['cases']}건) {pct(s['before'])}에서 {pct(s['after'])}로." for s in res["suppliers"][:3])
    b, a = res["before"], res["after"]
    p2 = (lambda v: "-" if v is None else f"{100 * v:.2f}%")
    return (f"정답 {res['cases']}건의 Main 칸 정확도가 {p2(b)}에서 {p2(a)}로 바뀝니다.{sups}"
            f" 바로잡는 칸 {res['fixed']}, 틀어지는 칸 {res['broken']}, 틀린 값으로 채우는 칸 {res['wrong']}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rule", help="규칙 id. 없으면 후보 모두")
    ap.add_argument("--all", action="store_true", help="화면에서 올린 시험 묶음의 정답까지")
    a = ap.parse_args()
    sets = A.load_sets(all_jobs=a.all)
    if not sets:
        sys.exit("확정한 정답이 없습니다.")
    rules = [R.get(a.rule)] if a.rule else [r for r in R.all_rules() if r["status"] == "candidate"]
    if not a.rule:
        allr = simulate_all(rules, sets)
        print(f"후보 {allr['rules']}개(코드 사전, 값 바꾸기)를 모두 반영하면: {text(allr)}"
              + (f" 미리 잴 수 없는 후보 {allr['skipped']}개는 뺐습니다." if allr["skipped"] else "") + "\n")
    for r in rules:
        if not r:
            sys.exit(f"규칙을 찾지 못했습니다: {a.rule}")
        print(f"[{R.KINDS.get(r['kind'], r['kind'])}, {R.supplier_name(r['scope'])}] {R.describe(r)}")
        res = simulate(r, sets)
        print("   ", text(res))
        for e in res.get("examples") or []:
            print("      ", e)
        if r["scope"] != R.COMMON and r["kind"] in ("code", "alias", "check"):
            print("    공통으로 반영하면:", text(simulate(r, sets, R.COMMON)))


if __name__ == "__main__":
    main()
