"""건별 폴더의 서류 묶음을 고객 화면 작업 목록에 한꺼번에 넣는다. 정답 세트(확정)를 만들 때 쓴다.

    python tools/bulk_jobs.py work/docs_raw/hx --dry-run         # 넣을 건만 본다
    python tools/bulk_jobs.py work/docs_raw/hx --order work/batch/20261110-083042.json

폴더 하나가 묶음 하나다(batch_check.py 와 같은 모양). 폴더 안 PDF 를 모두 한 작업으로 넣는다.

🔴 왜 디스크에 「대기」 작업으로 써 두기만 하나. 서버는 켜질 때 디스크에 대기로 남은 작업을 자기 줄에 다시
   세워 차례로 읽는다(wuxi_web.Store.load_all). 그러면 판독, 결과 저장, 확정, 학습이 화면에서 올린 작업과 한
   길로 간다. 이 도구가 따로 판독을 돌리면 서버와 동시에 Claude 를 불러 서로 느려지고, 결과 모양이 어긋날 수
   있다. 그래서 서버를 끈 채로 이 도구를 돌리고, 그다음 서버를 켠다. 서버가 켜져 있으면 다시 켜야 읽기 시작한다.
🔴 같은 건을 두 번 넣지 않는다. 작업에 건 이름(case)을 남기고, 이미 있는 건은 건너뛴다.
🔴 보관 기간이 짧으면 확정한 정답이 작업 폴더와 함께 지워진다(기본 14일). 경고만 하고 바꾸지는 않는다.
   보관 기간은 운영 화면 설정에서 사람이 바꾼다(이력에 남는다).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import socket
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wuxi_web  # noqa: E402

SOURCE = "bulk"


def case_of_file(name: str) -> str | None:
    """한곳에 모인 PDF 의 건 이름. 파일 이름의 첫 낱말이다(「SFSA2610153  正式件.pdf」 는 SFSA2610153).

    숫자가 네 자리 넘게 이어지지 않는 이름은 건 번호가 아니라 함께 받은 다른 자료로 보고 뺀다
    (10월 자료의 「日本车板图.pdf」, 「国外代理入库照.pdf」).
    """
    stem = os.path.splitext(name)[0].strip()
    first = stem.split()[0] if stem.split() else ""
    return first if re.search(r"\d{4,}", first) else None


def case_dirs(root: str, skipped: list | None = None) -> list[tuple[str, list[str]]]:
    """(건 이름, PDF 경로들). 건마다 폴더가 있으면 폴더 하나가 건 하나이고, 폴더 바로 안의 PDF 는 파일 하나가
    건 하나다(10월 자료처럼 한곳에 모아 받은 경우). 같은 건 이름의 파일은 한 건으로 묶는다. PDF 가 없는 폴더는 뺀다.
    skipped 를 주면 건 번호로 보지 않은 파일 이름을 담는다."""
    out, flat = [], {}
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            pdfs = sorted({f for f in glob.glob(os.path.join(p, "*")) if f.lower().endswith(".pdf")})
            if pdfs:
                out.append((d, pdfs))
        elif d.lower().endswith(".pdf"):
            case = case_of_file(d)
            if case:
                flat.setdefault(case, []).append(p)
            elif skipped is not None:
                skipped.append(d)
    have = {c for c, _ in out}
    out += [(c, sorted(ps)) for c, ps in sorted(flat.items()) if c not in have]
    return sorted(out)


def ordered(cases, batch_json: str | None):
    """일괄 점검 결과가 있으면 「이상 없음」 건을 앞에 둔다. 대조가 빨리 끝나 정답이 먼저 쌓인다."""
    if not batch_json:
        return cases
    with open(batch_json, encoding="utf-8") as fh:
        verdict = {r.get("case"): ((r.get("completeness") or {}).get("verdict") or "") for r in json.load(fh)}
    rank = {"이상 없음": 0, "빠진 줄 의심": 1}
    return sorted(cases, key=lambda c: (rank.get(verdict.get(c[0]), 2), c[0]))


def registered() -> set[str]:
    """이미 작업 목록에 있는 건 이름."""
    root = wuxi_web.jobs_root()
    have = set()
    if not root.is_dir():
        return have
    for d in root.iterdir():
        f = d / "job.json"
        if wuxi_web.JOB_ID.match(d.name) and f.is_file():
            try:
                job = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if job.get("case"):
                have.add(job["case"])
    return have


def make_job(case: str, pdfs: list[str], created: float) -> dict:
    """화면 업로드(wuxi_web.create_job)와 같은 모양의 대기 작업을 만든다."""
    if len(pdfs) > wuxi_web.MAX_FILES:
        raise ValueError(f"{case}: PDF 가 {len(pdfs)}개라 한 작업 한도({wuxi_web.MAX_FILES}개)를 넘습니다")
    jid = uuid.uuid4().hex
    d = wuxi_web.STORE.dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    names = []
    try:
        for i, p in enumerate(pdfs):
            if os.path.getsize(p) > wuxi_web.MAX_MB * 1024 * 1024:
                raise ValueError(f"{case}: {os.path.basename(p)} 가 {wuxi_web.MAX_MB}MB 를 넘습니다")
            with open(p, "rb") as fh:
                if not fh.read(1024).lstrip()[:5].startswith(b"%PDF"):
                    raise ValueError(f"{case}: {os.path.basename(p)} 는 PDF 가 아닙니다")
            name = wuxi_web.stored_name(i, os.path.basename(p))
            shutil.copyfile(p, d / name)
            names.append(name)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    job = {"id": jid, "status": "queued", "created": created, "names": names, "pdfs": [str(d / n) for n in names],
           "steps": [], "payload": None, "error": None, "title": case, "case": case, "source": SOURCE}
    wuxi_web.STORE.save(job)
    wuxi_web.log_access(None, "bulk", jid, note=f"{case} {len(names)} files")
    return job


def server_up(port: int = 8767) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="건별 폴더의 서류 묶음을 고객 화면 작업 목록에 대기 작업으로 넣습니다.")
    ap.add_argument("root", help="건별 폴더가 든 폴더(예: work/docs_raw/hx)")
    ap.add_argument("--order", help="일괄 점검 결과 JSON. 주면 「이상 없음」 건을 먼저 넣습니다")
    ap.add_argument("--cases", nargs="*", help="이 건들만 넣습니다")
    ap.add_argument("--limit", type=int, help="앞에서 이만큼만 넣습니다")
    ap.add_argument("--dry-run", action="store_true", help="넣지 않고 넣을 건만 보여 줍니다")
    a = ap.parse_args(argv)

    root = os.path.abspath(os.path.expanduser(a.root))
    if not os.path.isdir(root):
        print(f"폴더가 없습니다: {root}")
        return 2
    skipped = []
    cases = ordered(case_dirs(root, skipped), a.order)
    if a.cases:
        want = set(a.cases)
        cases = [c for c in cases if c[0] in want]
    have = registered()
    todo = [c for c in cases if c[0] not in have]
    skip = len(cases) - len(todo)
    if a.limit:
        todo = todo[:a.limit]

    print(f"작업 폴더: {wuxi_web.jobs_root()}")
    print(f"넣을 건 {len(todo)}건, PDF {sum(len(p) for _, p in todo)}개" + (f" (이미 있는 {skip}건은 건너뜀)" if skip else "")
          + (f", 남은 건 {len(cases) - skip - len(todo)}건" if a.limit else ""))
    if skipped:
        print(f"건 번호가 아니라 넣지 않은 PDF {len(skipped)}개: {', '.join(skipped[:10])}")
    days = wuxi_web.keep_days()
    if days < 90:
        print(f"주의: 보관 기간이 {days}일입니다. 그 뒤에는 확정한 정답도 지워집니다. 운영 화면 설정에서 늘려 주십시오.")
    if a.dry_run:
        for case, pdfs in todo:
            print(f"  {case}  PDF {len(pdfs)}개")
        return 0

    base = time.time()
    done = 0
    for k, (case, pdfs) in enumerate(todo):
        try:
            make_job(case, pdfs, base + k * 0.001)   # 넣은 차례대로 읽게 created 를 조금씩 늘린다
            done += 1
            print(f"  [{k + 1}/{len(todo)}] {case}  PDF {len(pdfs)}개")
        except Exception as e:
            print(f"  [{k + 1}/{len(todo)}] {case}  건너뜀: {e}")
    print(f"대기 작업 {done}건을 넣었습니다.")
    if server_up():
        print("서버가 켜져 있습니다. 서버 탭을 닫고 wuxi-start.ps1 로 다시 켜야 읽기 시작합니다.")
    else:
        print("wuxi-start.ps1 로 서버를 켜면 차례로 읽습니다. 다 읽힌 건부터 고객 화면 목록에서 열어 확정합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
