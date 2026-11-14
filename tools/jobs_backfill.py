"""이미 읽은 작업의 화면 값을 지금 코드에 맞춘다. 다시 읽지 않는다.

    python tools/jobs_backfill.py --dry-run
    python tools/jobs_backfill.py

하는 일 둘
  통화       통화 열(Currency)이 생기기 전에 읽은 작업의 품목 줄에 그 인보이스의 통화를 잇는다(gerp_xlsx.add_currency)
  줄 잇기    코드로 못 이은 포장명세서 줄(출처 PL)을 같은 인보이스의 같은 수량 줄에 합친다(gerp_xlsx.join_pl_rows)

🔴 왜 다시 읽지 않나. 두 일 모두 판독 때 이미 읽은 값을 옮기기만 한다. 다시 읽으면 Claude 사용량을 쓰고
   판독 값이 흔들려, 같은 기준으로 정확도를 잴 수 없다.
🔴 서버가 켜져 있으면 하지 않는다. 서버는 작업을 메모리에 들고 있다가 확정이나 엑셀 받기 때 그대로 다시 저장한다.
   켜진 채로 파일을 고치면 그때 고친 것이 덮인다.
🔴 엑셀을 받았거나 확정한 작업은 줄을 잇지 않는다. 사람이 남긴 값(final.json, golden.json)이 화면 줄 번호(_i)로
   원래 줄을 가리키는데, 줄을 합치면 번호가 어긋난다. 통화는 열만 더하므로 그런 작업에도 채운다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bulk_jobs  # noqa: E402
import gerp_xlsx  # noqa: E402
import wuxi_web  # noqa: E402


def has_human_values(job: dict) -> bool:
    d = wuxi_web.STORE.dir(job["id"])
    return bool(job.get("confirmed") or job.get("edits") or job.get("downloads")
                or (d / "final.json").is_file() or (d / "golden.json").is_file())


def fix_payload(p: dict, join: bool) -> tuple[int, int]:
    """(통화를 채운 줄, 합친 포장 줄)."""
    items = p.get("items") or []
    joined = gerp_xlsx.join_pl_rows(items) if join else 0
    filled = 0
    if "Currency" not in ((p.get("cols") or {}).get("extra") or []):
        filled = gerp_xlsx.add_currency(items, p.get("inv"))
        for r in items:
            if isinstance(r, dict):
                r.setdefault("Currency", "")
        p.setdefault("cols", {})["extra"] = list(gerp_xlsx.EXTRA_ITEM_COLS)
    return filled, joined


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="이미 읽은 작업의 화면 값을 지금 코드에 맞춥니다(다시 읽지 않음).")
    ap.add_argument("--dry-run", action="store_true", help="고치지 않고 몇 건, 몇 줄인지 봅니다")
    a = ap.parse_args(argv)
    if not a.dry_run and bulk_jobs.server_up():
        print("서버가 켜져 있습니다. 서버 창을 닫은 뒤 다시 돌려 주십시오(켜진 채 고치면 덮입니다).")
        return 2
    root = wuxi_web.jobs_root()
    n_job = n_cur = n_join = n_skip = 0
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        f = d / "job.json"
        if not (wuxi_web.JOB_ID.match(d.name) and f.is_file()):
            continue
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        p = job.get("payload")
        if not p:
            continue
        join = not has_human_values(job)
        if not join and any(r.get("출처") == "PL" for r in p.get("items") or []):
            n_skip += 1
        before = len(p.get("items") or [])
        cur, joined = fix_payload(p, join)
        if not (cur or joined):
            continue
        n_job += 1
        n_cur += cur
        n_join += joined
        print(f"  {job.get('case') or job['id'][:8]:14} 통화 {cur}줄  합친 포장 줄 {joined}  ({before}줄에서 {len(p['items'])}줄)")
        if not a.dry_run:
            wuxi_web.STORE.save(job)
    print(f"{'고칠' if a.dry_run else '고친'} 작업 {n_job}건, 통화를 채운 줄 {n_cur}, 합친 포장 줄 {n_join}")
    if n_skip:
        print(f"엑셀을 받았거나 확정한 작업 {n_skip}건은 줄을 잇지 않았습니다. 화면에서 PL 줄을 직접 정리합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
