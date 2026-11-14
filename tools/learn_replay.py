"""확정하거나 엑셀을 받은 작업의 사람 값으로 학습 후보를 새 기준으로 다시 만든다. AI 를 부르지 않는다.

후보 만드는 기준(wuxi_learn)이 바뀌면, 그 전에 쌓인 후보는 옛 기준으로 만든 것이다. 작업마다 그 작업이 남긴 후보 근거를
걷어 내고, 정답(golden.json, 없으면 final.json)과 AI 값의 차이로 후보를 다시 만든다. 근거가 모두 없어진 후보는 지운다(이력에 남음).

🔴 반영한 규칙(active)과 거절한 규칙은 건드리지 않는다. 빠짐 검사 후보(check)는 판정에서 나오므로 그대로 둔다.
   작업 파일(job.json)과 판독 기록(feedback.jsonl)은 고치지 않는다.

    python tools/learn_replay.py            # 바뀔 것만 보여 줌
    python tools/learn_replay.py --apply    # 실제로 고침
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wuxi_paths  # noqa: E402
import wuxi_rules as R  # noqa: E402
import wuxi_learn as L  # noqa: E402


def human_jobs():
    root = wuxi_paths.work("jobs")
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        try:
            with open(os.path.join(d, "job.json"), encoding="utf-8") as fh:
                job = json.load(fh)
        except (OSError, ValueError):
            continue
        for fn in ("golden.json", "final.json"):
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as fh:
                    items = json.load(fh).get("items") or []
            except (OSError, ValueError):
                continue
            job["pdfs"] = [os.path.join(d, n) for n in job.get("names") or []]
            yield job, items, fn
            break


def made_by(job, items) -> list:
    """record_edits 가 만들 후보(종류, 공급사, 값, 근거). 기록은 남기지 않는다."""
    d = L.diff(job, items)
    sid = job.get("supplier") or R.COMMON
    jid = job.get("id", "")[:12]
    out = []
    for c in d["cells"]:
        ev = {"job": jid, "time": job.get("confirmed") or job.get("created") or 0, "row": c["row"], "col": c["col"],
              "before": c["before"], "after": c["after"], "desc": c["desc"][:120], "invoice": c["invoice"], "supplier": sid,
              "replay": True}
        for kind, data in L.suggest(c, job):
            out.append((kind, sid, data, ev))
    if d["added"]:
        out.append(("guide", sid, {"text": L.MISSING_GUIDE, "label": L.MISSING_LABEL},
                    {"job": jid, "time": job.get("confirmed") or 0, "row": -1, "col": "", "before": "",
                     "after": f"사람이 줄 {len(d['added'])}개를 더함", "supplier": sid, "replay": True}))
    return out


def snapshot():
    return {r["id"]: (r["status"], R.describe(r), R.supplier_name(r["scope"]), len(r.get("evidence") or []))
            for r in R.all_rules(include_deleted=True) if r["kind"] in L.EDIT_KINDS}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    jobs = list(human_jobs())
    plan = [(job, made_by(job, items), fn) for job, items, fn in jobs]
    print(f"사람 값이 있는 작업 {len(jobs)}건")
    for job, made, fn in plan:
        if made:
            print(f"  {job.get('case') or job['id'][:8]:14} {fn:12} 후보 {len(made)}개")
            for kind, sid, data, _ in made:
                print(f"      {kind:6} {R.describe({'kind': kind, 'data': data})}")
    if not a.apply:
        print("\n바뀔 것만 보였습니다. 고치려면 --apply 를 붙입니다.")
        return
    before = snapshot()
    for job, made, _ in plan:
        R.drop_job_evidence(job["id"][:12], L.EDIT_KINDS, by="다시 만들기",
                            again=[(k, s, R.key_of(k, dt)) for k, s, dt, _ in made])
        for kind, sid, data, ev in made:
            R.upsert_candidate(kind, sid, data, ev, by="다시 만들기")
    after = snapshot()
    print("\n후보 바뀐 것")
    for rid in sorted(set(before) | set(after)):
        b, n = before.get(rid), after.get(rid)
        if b == n:
            continue
        if b is None:
            print(f"  새 후보   {n[2]} | {n[1]} (근거 {n[3]})")
        elif n[0] == "deleted" and b[0] != "deleted":
            print(f"  지움      {b[2]} | {b[1]} (근거 {b[3]})")
        else:
            print(f"  근거 바뀜 {n[2]} | {n[1]} (근거 {b[3]} → {n[3]}, {n[0]})")
    left = [r for r in R.all_rules() if r["status"] == "candidate" and r["kind"] in L.EDIT_KINDS]
    print(f"\n남은 후보 {len(left)}개")


if __name__ == "__main__":
    main()
