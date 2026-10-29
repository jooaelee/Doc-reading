#!/usr/bin/env bash
# 회사 노트북에서 우시 이관을 마무리한다. 순서와 판단할 곳은 docs/노트북에서-한-번에.md 에 있다.
#
#   tools/laptop_go.sh check          1. 준비 확인, 노트북과 사내 Git 에만 있는 우시 수정 찾기, docagent 흔적과 Claude 계정 보기
#   tools/laptop_go.sh ack <파일>...   1에서 「확인 필요」로 나온 파일을 다 봤다고 적는다(옮겼거나 옮길 것이 없을 때)
#   tools/laptop_go.sh setup          2. 가상환경, 패키지, 자동 시험
#   tools/laptop_go.sh verify         3. 실제 AI 판독(가짜 묶음 세 가지)과 노트북의 실제 고객 서류 몇 건
#   tools/laptop_go.sh sync           4. 사내 Git master 의 wuxi_ocr/ 를 GitHub 판으로 덮어쓰고 올린다(1~3 이 통과한 판만)
#   tools/laptop_go.sh serve          5. 새 판을 따로 띄운다(끝내려면 Ctrl+C)
#
# 바꿀 수 있는 값(환경변수)
#   COMPANY_REPO   사내 저장소 사본. 기본 C:/Users/SAMSUNG/KEROL_FMS_DOC_AI
#   WUXI_GH_REF    GitHub 쪽 기준. 기본 origin/main
#   WUXI_BASE      사내 이관 커밋. 이 뒤에 사내 쪽에서 바꾼 파일만 본다. 기본 e63db149
#   WUXI_BATCH     실제 서류 몇 건을 읽을지. 기본 5
#
# 하지 않는 것: 사내 작업 폴더의 갈래와 파일 바꾸기, docagent 코드 고치기, 비밀 파일(.env, 키) 읽기, .65 배포.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DST="${COMPANY_REPO:-C:/Users/SAMSUNG/KEROL_FMS_DOC_AI}"
GH_REF="${WUXI_GH_REF:-origin/main}"
BASE_REF="${WUXI_BASE:-e63db149}"
BATCH="${WUXI_BATCH:-5}"
SUB=wuxi_ocr
ST="$SRC/work/laptop"
mkdir -p "$ST"

die() { echo "멈춤: $*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }
hr() { printf '\n== %s\n' "$*"; }

gh_sha() { git -C "$SRC" rev-parse -q --verify "$GH_REF^{commit}" || die "GitHub 기준 $GH_REF 를 찾지 못했습니다"; }
stamp_ok() { [ -f "$ST/$1.ok" ] && [ "$(cat "$ST/$1.ok")" = "$2" ]; }

pick_python() {
  local c v
  for c in "$SRC/.venv/Scripts/python.exe" "$SRC/.venv/bin/python"; do
    [ -x "$c" ] && { echo "$c"; return; }
  done
  for c in "py -3.13" "py -3.12" "py -3.11" "py -3" python3 python; do
    v="$($c -c 'import sys; print("%d %d" % sys.version_info[:2])' 2>/dev/null)" || continue
    set -- $v
    if [ "$1" -eq 3 ] && [ "$2" -ge 11 ]; then echo "$c"; return; fi
  done
  return 1
}
venv_python() {
  local c
  for c in "$SRC/.venv/Scripts/python.exe" "$SRC/.venv/bin/python"; do [ -x "$c" ] && { echo "$c"; return; }; done
  die "가상환경이 없습니다. 먼저 tools/laptop_go.sh setup 을 실행하십시오"
}

# ───────────────────────────── 1. check
# 사내 쪽(사내 원격의 갈래, 노트북의 로컬 갈래, 노트북 작업 폴더)에서 이관 커밋 뒤에 바뀐 wuxi_ocr/ 파일을 모아
# GitHub 판과 견준다. 같거나 숫자만 다르면(사내 판은 실제 번호를 같은 꼴의 가짜 값으로 바꿨다) 넘어가고,
# 나머지는 「확인 필요」로 적는다. 숫자만 다른 파일은 GitHub 쪽이 실제 번호를 가진 원본이다.
norm() { tr -d '\r' | sed 's/[0-9]/0/g'; }

classify() {   # $1 원천 이름, $2 상태(A/M/D), $3 경로(wuxi_ocr/...)
  local src="$1" stt="$2" path="$3" rel="${3#$SUB/}" blob gblob tmpc tmpg
  case "$rel" in work/*|data/*|bin/*|.venv/*|*/__pycache__/*|__pycache__/*|*.pyc) return ;; esac
  gblob="$(git -C "$SRC" rev-parse -q --verify "$GH:$rel" 2>/dev/null || true)"
  if [ "$stt" = D ]; then
    blob="deleted"
    [ -z "$gblob" ] && return
    grep -qxF "$blob	$path" "$ST/reviewed.txt" 2>/dev/null && { echo "reviewed" >> "$ST/count.txt"; return; }
    printf '%s\t%s\t%s\t%s\n' "$blob" "$path" "$src" "사내에서 지웠는데 GitHub 에 있음" >> "$ST/review.tsv"
    return
  fi
  if [ "$src" = "작업 폴더" ]; then
    [ -f "$DST/$path" ] || return
    blob="$(git -C "$DST" hash-object --path="$path" "$DST/$path")"
  else
    blob="$(git -C "$DST" rev-parse "$src:$path")"
  fi
  grep -q "^$blob	$path	" "$ST/review.tsv" 2>/dev/null && return          # 다른 원천에서 이미 본 같은 내용
  grep -qxF "$blob	$path" "$ST/seen.txt" 2>/dev/null && return
  printf '%s\t%s\n' "$blob" "$path" >> "$ST/seen.txt"
  if [ "$blob" = "$gblob" ]; then echo same >> "$ST/count.txt"; return; fi
  if grep -qxF "$blob	$path" "$ST/reviewed.txt" 2>/dev/null; then echo reviewed >> "$ST/count.txt"; return; fi
  if [ -z "$gblob" ]; then
    printf '%s\t%s\t%s\t%s\n' "$blob" "$path" "$src" "사내에만 있음" >> "$ST/review.tsv"; return
  fi
  tmpc="$ST/.c"; tmpg="$ST/.g"
  if [ "$src" = "작업 폴더" ]; then norm < "$DST/$path" > "$tmpc"; else git -C "$DST" show "$src:$path" | norm > "$tmpc"; fi
  git -C "$SRC" show "$GH:$rel" | norm > "$tmpg"
  if cmp -s "$tmpc" "$tmpg"; then echo masked >> "$ST/count.txt"; return; fi
  printf '%s\t%s\t%s\t%s\n' "$blob" "$path" "$src" "내용이 다름" >> "$ST/review.tsv"
}

collect() {    # 확인할 파일을 원천별로 모은다
  : > "$ST/review.tsv"; : > "$ST/seen.txt"; : > "$ST/count.txt"; touch "$ST/reviewed.txt"
  local ref line stt path
  if [ -n "$BASE" ]; then
    while IFS= read -r ref; do
      [ -n "$ref" ] || continue
      if ! git -C "$DST" merge-base --is-ancestor "$BASE" "$ref" 2>/dev/null; then
        if git -C "$DST" log -1 --format=%h "$ref" -- "$SUB" 2>/dev/null | grep -q . \
           && ! grep -qxF -e "-	$ref" "$ST/reviewed.txt"; then
          printf '%s\t%s\t%s\t%s\n' "-" "$ref" "$ref" "이관 커밋이 없는 갈래에 wuxi_ocr 수정이 있음" >> "$ST/review.tsv"
        fi
        continue
      fi
      git -c core.quotepath=off -C "$DST" diff --name-status --no-renames "$BASE" "$ref" -- "$SUB" \
        | while IFS=$'\t' read -r stt path; do classify "$ref" "${stt:0:1}" "$path"; done
    done < <( { echo origin/master; git -C "$DST" for-each-ref --format='%(refname:short)' refs/heads refs/remotes \
                | grep -v -e '/HEAD$' -e '^origin/master$' || true; } )
    git -c core.quotepath=off -C "$DST" diff --name-status --no-renames "$BASE" -- "$SUB" \
      | while IFS=$'\t' read -r stt path; do classify "작업 폴더" "${stt:0:1}" "$path"; done
  else
    say "이관 커밋 $BASE_REF 를 찾지 못해 사내 원격 master 의 $SUB/ 전체를 견줍니다(확인할 것이 많아집니다)."
    git -c core.quotepath=off -C "$DST" ls-tree -r --name-only "origin/master" -- "$SUB" \
      | while IFS= read -r path; do classify "origin/master" M "$path"; done
    git -c core.quotepath=off -C "$DST" diff --name-status --no-renames HEAD -- "$SUB" \
      | while IFS=$'\t' read -r stt path; do classify "작업 폴더" "${stt:0:1}" "$path"; done
  fi
  git -c core.quotepath=off -C "$DST" ls-files --others --exclude-standard -- "$SUB" \
    | while IFS= read -r path; do classify "작업 폴더" A "$path"; done
}

write_patches() {
  local n=0 blob path src why out rel
  rm -rf "$ST/review"; mkdir -p "$ST/review"
  while IFS=$'\t' read -r blob path src why; do
    n=$((n + 1)); rel="${path#$SUB/}"; out="$ST/review/$(printf '%02d' $n)"
    case "$why" in
      "내용이 다름")
        if [ "$src" = "작업 폴더" ]; then
          git -C "$DST" diff "${BASE:-HEAD}" -- "$path" > "$out-사내변경.patch" || true
          tr -d '\r' < "$DST/$path" > "$out-사내판.txt"
        else
          git -C "$DST" diff "${BASE:-$src}" "$src" -- "$path" > "$out-사내변경.patch" || true
          git -C "$DST" show "$src:$path" | tr -d '\r' > "$out-사내판.txt"
        fi ;;
      "사내에만 있음")
        if [ "$src" = "작업 폴더" ]; then tr -d '\r' < "$DST/$path" > "$out-사내판.txt"; else git -C "$DST" show "$src:$path" > "$out-사내판.txt"; fi ;;
    esac
    printf '%02d  %-26s %-18s %s\n' "$n" "$why" "$src" "$path"
  done < "$ST/review.tsv"
}

cmd_check() {
  hr "도구"
  git --version
  PYC="$(pick_python || true)"
  [ -n "$PYC" ] && say "파이썬: $($PYC --version 2>&1) ($PYC)" || say "파이썬 3.11 이상을 찾지 못했습니다. 설치한 뒤 다시 실행하십시오(setup 에서 멈춥니다)."
  if command -v claude >/dev/null 2>&1; then say "Claude Code: $(claude --version 2>/dev/null | head -1)"; else say "Claude Code CLI 를 찾지 못했습니다(verify 에서 멈춥니다)."; fi

  hr "GitHub 판"
  if [ "${GH_REF#origin/}" != "$GH_REF" ]; then git -C "$SRC" fetch -q origin "${GH_REF#origin/}" || die "GitHub 에서 받지 못했습니다(인터넷, GitHub 로그인 확인)"; fi
  GH="$(gh_sha)"
  local dirty; dirty="$(git -C "$SRC" status --porcelain --untracked-files=no)"
  [ -z "$dirty" ] || die "이 저장소에 커밋하지 않은 변경이 있습니다. 커밋하고 GitHub 에 올린 뒤 다시 실행하십시오:
$dirty"
  local head; head="$(git -C "$SRC" rev-parse HEAD)"
  if [ "$head" != "$GH" ]; then
    if git -C "$SRC" merge-base --is-ancestor "$head" "$GH"; then git -C "$SRC" merge -q --ff-only "$GH"; say "GitHub 판으로 받아 왔습니다."
    else die "이 저장소에 GitHub 에 없는 커밋이 있습니다. 먼저 GitHub 에 올리십시오(git push)"; fi
  fi
  say "GitHub $GH_REF ${GH:0:8}: $(git -C "$SRC" log -1 --format=%s "$GH")"

  hr "사내 저장소"
  [ -d "$DST" ] || die "사내 저장소 사본이 없습니다: $DST (COMPANY_REPO 로 바꿀 수 있습니다)"
  git -C "$DST" rev-parse --git-dir >/dev/null 2>&1 || die "$DST 가 Git 저장소가 아닙니다"
  git -C "$DST" fetch -q origin master || die "사내 Git 에서 master 를 받지 못했습니다(사내망 연결 확인)"
  local cm; cm="$(git -C "$DST" rev-parse origin/master)"
  say "사내 origin/master ${cm:0:8}, 작업 폴더 갈래 $(git -C "$DST" branch --show-current 2>/dev/null || echo '(분리됨)')"
  BASE="$(git -C "$DST" rev-parse -q --verify "$BASE_REF^{commit}" 2>/dev/null || true)"
  [ -n "$BASE" ] && say "이관 커밋 ${BASE:0:8}: $(git -C "$DST" log -1 --format='%ad %s' --date=short "$BASE")"

  hr "노트북과 사내 Git 에만 있는 우시 수정"
  collect
  local same masked reviewed
  same=$(grep -c '^same$' "$ST/count.txt" || true); masked=$(grep -c '^masked$' "$ST/count.txt" || true)
  reviewed=$(grep -c '^reviewed$' "$ST/count.txt" || true)
  say "GitHub 과 같음 $same개, 실제 번호를 가린 차이만 있음 $masked개, 앞서 확인함 $reviewed개"
  local nrev; nrev=$(wc -l < "$ST/review.tsv" | tr -d ' ')
  if [ "$nrev" -gt 0 ]; then
    say "확인 필요 $nrev개(자료: $ST/review/):"
    write_patches
  else
    say "확인 필요 없음. 사내 쪽에만 있는 우시 수정이 없습니다."
  fi

  hr "docagent 쪽 우시 흔적(보기만 함)"
  local tr1 tr2
  tr1="$(git -C "$DST" grep -n -i -I 'wuxi' origin/master -- . ":(exclude)$SUB" 2>/dev/null | cut -c1-170 | head -30 || true)"
  tr2="$(git -C "$DST" grep -n -i -I --untracked 'wuxi' -- . ":(exclude)$SUB" ':(exclude)*.log' 2>/dev/null | cut -c1-170 | head -30 || true)"
  [ -n "$tr1" ] && { say "[사내 origin/master]"; say "$tr1"; } || say "[사내 origin/master] 없음"
  [ -n "$tr2" ] && { say "[노트북 작업 폴더]"; say "$tr2"; } || say "[노트북 작업 폴더] 없음"

  hr "이 PC 의 Claude 계정(보기만 함)"
  if command -v claude >/dev/null 2>&1; then
    local d
    for d in "" "$HOME"/.claude-* /c/docai/*claude* C:/docai/*claude*; do
      [ -z "$d" ] || [ -d "$d" ] || continue
      local out em
      if [ -z "$d" ]; then out="$(claude auth status 2>&1 || true)"; else out="$(CLAUDE_CONFIG_DIR="$d" claude auth status 2>&1 || true)"; fi
      local cj="${d:-$HOME}/.claude.json"     # 화면의 설정 탭과 같은 곳에서 이메일만 읽는다(비밀값은 안 읽음)
      em="$(grep -o '"emailAddress": *"[^"]*"' "$cj" 2>/dev/null | head -1 | sed 's/.*"\([^"]*\)"$/\1/' || true)"
      [ -z "$em" ] || em="$em ($(printf '%s' "$out" | grep -o '"loggedIn": *[a-z]*' | head -1 | sed 's/"loggedIn": */로그인 /' || true))"
      [ -n "$em" ] || em="$(printf '%s' "$out" | grep -o '"loggedIn": *[a-z]*' | head -1 || true)"
      [ -n "$em" ] || em="$(printf '%s' "$out" | head -1 | cut -c1-80 || true)"
      say "  ${d:-기본 계정(윈도 사용자)}: ${em:-알 수 없음}"
    done
  fi
  say "  봇이 어느 계정을 쓰는지는 C:\\docai\\bot.env 의 두 값으로 정해집니다(비밀 파일이라 열지 않았습니다)."

  hr "포트"
  if curl -s -o /dev/null -m 3 http://127.0.0.1:8767/; then say "8767 에 이미 무엇이 떠 있습니다. serve 는 비어 있는 다음 포트를 씁니다."; else say "8767 비어 있음"; fi

  if [ "$nrev" -gt 0 ]; then
    rm -f "$ST/check.ok"
    say ""
    say "결과: 확인할 파일이 있어 여기서 멈춥니다. 안내서 1단계대로 옮기거나 확인한 뒤 ack 하고 check 를 다시 실행하십시오."
    exit 3
  fi
  printf '%s %s\n' "$GH" "$cm" > "$ST/check.ok"
  say ""
  say "결과: 통과. 다음은 tools/laptop_go.sh setup"
}

cmd_ack() {
  [ $# -gt 0 ] || die "확인한 파일 경로를 주십시오(예: wuxi_ocr/tools/docpipe.py)"
  [ -f "$ST/review.tsv" ] || die "먼저 check 를 실행하십시오"
  local p q n=0
  for p in "$@"; do
    q="$p"; case "$q" in "$SUB"/*) ;; *) q="$SUB/$q" ;; esac
    while IFS=$'\t' read -r blob path src why; do
      if [ "$path" = "$q" ] || [ "$path" = "$p" ]; then printf '%s\t%s\n' "$blob" "$path" >> "$ST/reviewed.txt"; n=$((n + 1)); fi
    done < "$ST/review.tsv"
  done
  say "확인했다고 적었습니다: $n건. check 를 다시 실행하십시오."
}

# ───────────────────────────── 2. setup
cmd_setup() {
  GH="$(gh_sha)"
  [ "$(git -C "$SRC" rev-parse HEAD)" = "$GH" ] || die "이 저장소가 GitHub $GH_REF 판이 아닙니다. check 를 먼저 실행하십시오"
  local py; py="$(pick_python)" || die "파이썬 3.11 이상이 없습니다"
  if [ ! -x "$SRC/.venv/Scripts/python.exe" ] && [ ! -x "$SRC/.venv/bin/python" ]; then
    hr "가상환경 만들기"; $py -m venv "$SRC/.venv"
  fi
  local vp; vp="$(venv_python)"
  hr "패키지"
  "$vp" -m pip install -q --disable-pip-version-check -r "$SRC/requirements.txt" -r "$SRC/requirements-dev.txt"
  hr "자동 시험(AI 를 부르지 않음)"
  (cd "$SRC" && PYTHONIOENCODING=utf-8 "$vp" -m pytest -q -p no:cacheprovider tests) || die "자동 시험이 실패했습니다"
  echo "$GH" > "$ST/setup.ok"
  say ""
  say "결과: 통과. 다음은 tools/laptop_go.sh verify (몇 분 걸립니다)"
}

# ───────────────────────────── 3. verify
cmd_verify() {
  GH="$(gh_sha)"
  stamp_ok setup "$GH" || die "setup 이 이 판에서 통과하지 않았습니다"
  local vp; vp="$(venv_python)"
  export PYTHONIOENCODING=utf-8
  local bad=0
  hr "실제 AI 판독 1: 글자 PDF"
  (cd "$SRC" && "$vp" tools/make_sample.py >/dev/null && "$vp" tools/selfcheck.py) || bad=1
  hr "실제 AI 판독 2: 스캔 PDF(OCR)"
  (cd "$SRC" && "$vp" tools/selfcheck.py --scan) || bad=1
  hr "실제 AI 판독 3: 120줄, 인보이스 세 장(빠짐 시험)"
  (cd "$SRC" && "$vp" tools/make_sample.py --lines 120 --invoices 3 --name long >/dev/null && "$vp" tools/selfcheck.py --name long) || bad=1
  [ "$bad" = 0 ] || die "가짜 묶음 판독이 정답과 다릅니다. 위 출력의 「빠짐」, 「틀림」 줄을 보십시오"

  hr "실제 고객 서류 $BATCH건"
  local raw="$DST/$SUB/work/docs_raw/hx"
  [ -d "$raw" ] || raw="$(ls -d "$DST/$SUB/work/docs_raw"/*/ 2>/dev/null | head -1 || true)"
  if [ -z "$raw" ] || [ ! -d "$raw" ]; then
    say "노트북에서 실제 고객 서류 폴더(work/docs_raw)를 찾지 못해 건너뜁니다."
  else
    say "서류 폴더: $raw (옮기지 않고 그 자리에서 읽습니다)"
    (cd "$SRC" && "$vp" tools/batch_check.py "$raw" --limit "$BATCH") || die "일괄 점검이 중간에 멈췄습니다"
    "$vp" - "$SRC/work/batch" "$DST/$SUB/work/batch" <<'PY' || die "실제 서류 점검에서 문제가 나왔습니다(위 출력)"
import glob, json, os, sys
new_dir, old_dir = sys.argv[1], sys.argv[2]
new = json.load(open(max(glob.glob(os.path.join(new_dir, "*.json")), key=os.path.getmtime), encoding="utf-8"))
errs = [r["case"] for r in new if r.get("error")]
print(f"새 판: {len(new)}건, 오류 {len(errs)}건")
bad = bool(errs)
olds = sorted(glob.glob(os.path.join(old_dir, "*.json")), key=os.path.getmtime)
old = {}
for f in olds:                       # 오래된 것부터 덮어 써서 건마다 가장 최근 결과를 쓴다
    try:
        for r in json.load(open(f, encoding="utf-8")):
            if not r.get("error"):
                old[r["case"]] = r
    except Exception:
        pass
both = [r for r in new if r["case"] in old and not r.get("error")]
if not both:
    print("예전 판의 점검 결과가 없어 견주지 않았습니다.")
for i, r in enumerate(both, 1):
    o = old[r["case"]]
    a, b = (o.get("lines") or {}), (r.get("lines") or {})
    va, vb = (o.get("completeness") or {}).get("verdict"), (r.get("completeness") or {}).get("verdict")
    diff = [k for k in ("ci_lines", "pl_lines", "cn_lines") if a.get(k) != b.get(k)]
    fewer = (b.get("ci_lines") or 0) < (a.get("ci_lines") or 0)
    mark = "줄이 줄어듦" if fewer else ("다름" if diff or va != vb else "같음")
    print(f"  건{i:03d} 예전 {va} 인보이스줄 {a.get('ci_lines')} / 새 판 {vb} 인보이스줄 {b.get('ci_lines')}  {mark}")
    bad = bad or fewer
sys.exit(1 if bad else 0)
PY
  fi
  echo "$GH" > "$ST/verify.ok"
  say ""
  say "결과: 통과. 다음은 tools/laptop_go.sh sync"
}

# ───────────────────────────── 4. sync
cmd_sync() {
  GH="$(gh_sha)"
  git -C "$DST" fetch -q origin master || die "사내 Git 에서 master 를 받지 못했습니다"
  local cm; cm="$(git -C "$DST" rev-parse origin/master)"
  stamp_ok check "$GH $cm" || die "check 뒤에 GitHub 이나 사내 master 가 바뀌었습니다. check 부터 다시 실행하십시오"
  stamp_ok setup "$GH" || die "setup 이 이 판에서 통과하지 않았습니다"
  stamp_ok verify "$GH" || die "verify 가 이 판에서 통과하지 않았습니다"
  hr "사내 Git 덮어쓰기"
  "$SRC/tools/sync_company_git.sh" "$DST" --ref "$GH_REF" --push
  git -C "$DST" fetch -q origin master
  echo "$GH $(git -C "$DST" rev-parse origin/master)" > "$ST/sync.ok"
  say ""
  say "결과: 사내 origin/master 가 GitHub ${GH:0:8} 판이 되었습니다. 노트북 작업 폴더($DST)는 건드리지 않았습니다."
}

# ───────────────────────────── 5. serve
cmd_serve() {
  local vp port=8767; vp="$(venv_python)"
  while curl -s -o /dev/null -m 2 "http://127.0.0.1:$port/"; do port=$((port + 1)); done
  say "고객 화면  http://127.0.0.1:$port/"
  say "운영 화면  http://127.0.0.1:$port/admin  (처음 열면 이 PC 에서 운영 비밀번호와 고객 화면 비밀번호를 함께 정합니다)"
  say "작업 폴더  $SRC/work"
  (sleep 3; "$vp" -c "import webbrowser; webbrowser.open('http://127.0.0.1:$port/admin')" >/dev/null 2>&1 || true) &
  cd "$SRC" && PYTHONIOENCODING=utf-8 exec "$vp" tools/wuxi_web.py serve --port "$port"
}

case "${1:-}" in
  check) cmd_check ;;
  ack) shift; cmd_ack "$@" ;;
  setup) cmd_setup ;;
  verify) cmd_verify ;;
  sync) cmd_sync ;;
  serve) cmd_serve ;;
  *) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
