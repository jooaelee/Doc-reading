#!/usr/bin/env bash
# GitHub commet/wuxi-ocr 의 판으로 사내 Git(DT/KEROL_FMS_DOC_AI)의 wuxi_ocr/ 폴더를 통째로 맞춘다.
#
#   tools/sync_company_git.sh <사내 저장소 경로> --compare   먼저 비교만(아무것도 안 바꿈)
#   tools/sync_company_git.sh <사내 저장소 경로> [--ref origin/main] [--branch master] [--push]
#
# - 사내 저장소의 작업 폴더(띄워 둔 docagent 사본)는 건드리지 않는다. 사내 원격의 대상 갈래를 임시 worktree 로
#   따로 꺼내 그 안에서만 커밋한다. 그래서 작업 폴더에 고치던 것이 있어도 섞이지 않는다.
# - wuxi_ocr/ 밖은 한 글자도 바꾸지 않는다. 바뀐 파일이 밖에 하나라도 있으면 커밋 전에 멈춘다.
# - GitHub 에 없는 파일은 사내 쪽에서도 지운다(덮어쓰기). 다만 사내에서만 추적하던 비밀번호 해시
#   (access.json, admin.json)는 남긴다. 지우면 띄워 둔 서버에 아무도 못 들어간다.
# - --push 가 없으면 커밋까지만 하고 멈춘다. 검토한 뒤 안내대로 올린다.
# - 되돌리기는 사내 저장소에서 git revert <커밋> 한 번이다.
set -euo pipefail

SUB=wuxi_ocr
REMOTE=origin
BRANCH=master
REF=origin/main
KEEP="access.json admin.json"
PUSH=0
FETCH=1
COMPARE=0
DST=""

die() { echo "멈춤: $*" >&2; exit 1; }
usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-2}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:?}"; shift 2 ;;
    --branch) BRANCH="${2:?}"; shift 2 ;;
    --remote) REMOTE="${2:?}"; shift 2 ;;
    --push) PUSH=1; shift ;;
    --compare) COMPARE=1; shift ;;
    --no-fetch) FETCH=0; shift ;;
    -h|--help) usage 0 ;;
    -*) die "모르는 선택지 $1" ;;
    *) [ -z "$DST" ] || die "사내 저장소 경로는 하나만 줍니다"; DST="$1"; shift ;;
  esac
done
[ -n "$DST" ] || usage

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DST="$(cd "$DST" && pwd)" || die "사내 저장소 경로가 없습니다"

# 1. GitHub 판
git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1 || die "$SRC 가 Git 저장소가 아닙니다"
if [ "$FETCH" = 1 ] && [ "${REF#origin/}" != "$REF" ]; then
  git -C "$SRC" fetch -q origin "${REF#origin/}" || die "GitHub 에서 ${REF#origin/} 을 받지 못했습니다"
fi
SHA="$(git -C "$SRC" rev-parse --verify -q "$REF^{commit}")" || die "GitHub 판 $REF 를 찾지 못했습니다"
git -C "$SRC" cat-file -e "$SHA:tools/wuxi_paths.py" 2>/dev/null || die "$REF 는 우시 저장소 판이 아닙니다"
TITLE="$(git -C "$SRC" log -1 --format=%s "$SHA")"

# 2. 사내 판
git -C "$DST" rev-parse --git-dir >/dev/null 2>&1 || die "$DST 가 Git 저장소가 아닙니다"
git -C "$DST" remote get-url "$REMOTE" >/dev/null 2>&1 || die "사내 저장소에 원격 $REMOTE 가 없습니다"
if [ "$FETCH" = 1 ]; then
  git -C "$DST" fetch -q "$REMOTE" "$BRANCH" || die "사내 원격에서 $BRANCH 를 받지 못했습니다(사내망 연결 확인)"
fi
BASE="$(git -C "$DST" rev-parse --verify -q "$REMOTE/$BRANCH^{commit}")" || die "사내 $REMOTE/$BRANCH 가 없습니다"
git -C "$DST" cat-file -e "$BASE:$SUB/tools/wuxi_paths.py" 2>/dev/null \
  || die "사내 $BRANCH 에 $SUB/ 가 없습니다. 경로나 갈래를 확인하십시오"

# 비교만: 사내 원격 판, 사내 작업 폴더에서 아직 커밋하지 않은 것, GitHub 판을 나란히 본다
if [ "$COMPARE" = 1 ]; then
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  mkdir -p "$TMP/company" "$TMP/github"
  git -C "$DST" archive "$BASE" "$SUB" | tar -x -C "$TMP/company"
  git -C "$SRC" archive "$SHA" | tar -x -C "$TMP/github"
  echo "사내 $REMOTE/$BRANCH ${BASE:0:8} 의 $SUB/ 와 GitHub $REF ${SHA:0:8} 비교"
  (cd "$TMP/company/$SUB" && find . -type f | sed 's#^\./##' | LC_ALL=C sort) > "$TMP/c.txt"
  (cd "$TMP/github" && find . -type f | sed 's#^\./##' | LC_ALL=C sort) > "$TMP/g.txt"
  show() { if [ -s "$1" ]; then sed 's/^/  /' "$1"; else echo "  없음"; fi; }
  LC_ALL=C comm -23 "$TMP/c.txt" "$TMP/g.txt" > "$TMP/only_all.txt"
  : > "$TMP/only_c.txt"; : > "$TMP/kept.txt"
  while IFS= read -r f; do
    case " $KEEP " in *" $f "*) echo "$f" >> "$TMP/kept.txt" ;; *) echo "$f" >> "$TMP/only_c.txt" ;; esac
  done < "$TMP/only_all.txt"
  LC_ALL=C comm -13 "$TMP/c.txt" "$TMP/g.txt" > "$TMP/only_g.txt"
  : > "$TMP/diff.txt"
  LC_ALL=C comm -12 "$TMP/c.txt" "$TMP/g.txt" | while IFS= read -r f; do
    cmp -s "$TMP/company/$SUB/$f" "$TMP/github/$f" || echo "$f" >> "$TMP/diff.txt"
  done
  git -c core.quotepath=off -C "$DST" status --short -- "$SUB" | grep -v "^?? $SUB/work/" > "$TMP/dirty.txt" || true
  echo
  echo "[사내에만 있는 파일] 맞추면 지워집니다. 필요한 것은 GitHub 에 먼저 옮깁니다"
  show "$TMP/only_c.txt"
  [ ! -s "$TMP/kept.txt" ] || echo "  사내에서만 추적하는 비밀번호 해시($(paste -sd, "$TMP/kept.txt"))는 맞출 때 남깁니다"
  echo
  echo "[양쪽에 있고 내용이 다른 파일] 사내에서만 고친 것이 있는지 봅니다(git diff --no-index 로 자세히)"
  show "$TMP/diff.txt"
  echo
  echo "[사내 작업 폴더에서 커밋하지 않은 $SUB/ 변경] 원격에 없으니 맞추기에 들어가지 않습니다"
  show "$TMP/dirty.txt"
  echo
  echo "[GitHub 에만 있는 파일] 새로 들어갑니다: $(wc -l < "$TMP/only_g.txt" | tr -d ' ')개"
  sed -n '1,15s/^/  /p' "$TMP/only_g.txt"
  [ "$(wc -l < "$TMP/only_g.txt")" -le 15 ] || echo "  (나머지 생략)"
  exit 0
fi

# 3. 임시 worktree 에서 맞추기
WT="$(dirname "$DST")/$(basename "$DST")-wuxi-sync"
[ ! -e "$WT" ] || die "지난번 임시 폴더가 남아 있습니다: $WT
  치우기: git -C \"$DST\" worktree remove --force \"$WT\""
git -C "$DST" worktree add -q --detach "$WT" "$BASE"
cleanup() { git -C "$DST" worktree remove --force "$WT" >/dev/null 2>&1 || true; }
KEEP_WT=0
trap '[ "$KEEP_WT" = 1 ] || cleanup' EXIT   # 커밋 전에 멈추면 임시 폴더를 치운다

git -C "$WT" rm -r -q "$SUB"
mkdir -p "$WT/$SUB"
git -C "$SRC" archive "$SHA" | tar -x -C "$WT/$SUB"

kept=""
for f in $KEEP; do
  if git -C "$WT" cat-file -e "$BASE:$SUB/$f" 2>/dev/null && [ ! -e "$WT/$SUB/$f" ]; then
    git -C "$WT" checkout -q "$BASE" -- "$SUB/$f"
    kept="$kept $f"
  fi
done

git -C "$WT" add -A "$SUB"
outside="$(git -c core.quotepath=off -C "$WT" diff --cached --name-only | grep -v "^$SUB/" || true)"
if [ -n "$outside" ]; then
  die "$SUB/ 밖의 파일이 바뀌려 합니다. 아무것도 커밋하지 않았습니다:
$outside"
fi
if git -C "$WT" diff --cached --quiet; then
  echo "사내 $BRANCH 의 $SUB/ 가 이미 GitHub ${SHA:0:8} 판과 같습니다. 할 일이 없습니다."
  exit 0
fi

echo "바뀌는 파일(사내 $BRANCH ${BASE:0:8} 에서 GitHub ${SHA:0:8} 로):"
git -c core.quotepath=off -C "$WT" diff --cached --stat=100 | tail -n 40
[ -z "$kept" ] || echo "사내에서만 추적하던 비밀번호 해시(${kept# })는 남겼습니다."

git -C "$WT" commit -q \
  -m "$SUB: GitHub commet/wuxi-ocr ${SHA:0:8} 판으로 맞춘다" \
  -m "원본: https://github.com/commet/wuxi-ocr/commit/$SHA
마지막 커밋: $TITLE

$SUB/ 폴더만 바꿨다. 되돌리려면 이 커밋을 revert 한다."
NEW="$(git -C "$WT" rev-parse HEAD)"
KEEP_WT=1
echo

if [ "$PUSH" = 1 ]; then
  git -C "$WT" push -q "$REMOTE" "HEAD:$BRANCH" || die "올리지 못했습니다. 임시 폴더 $WT 에 커밋이 남아 있습니다"
  KEEP_WT=0
  echo "커밋(${NEW:0:8})을 사내 $REMOTE/$BRANCH 에 올렸습니다. 서버에서 받아 오고 다시 띄우십시오(docs/사내-Git-맞추기.md 4단계)."
else
  echo "커밋했습니다: ${NEW:0:8}. 아직 사내 원격에 올리지 않았습니다."
  cat <<EOF

검토:    git -C "$WT" show --stat HEAD
올리기:  git -C "$WT" push $REMOTE HEAD:$BRANCH
치우기:  git -C "$DST" worktree remove --force "$WT"
EOF
fi
