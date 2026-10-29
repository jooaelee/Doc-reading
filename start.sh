#!/usr/bin/env bash
# 우시 서류 판독을 처음부터 띄운다(맥, 리눅스). 두 번째부터도 이 파일 하나로 띄운다.
#   ./start.sh            가상환경을 만들고 패키지를 받은 뒤, 비밀번호와 Claude 로그인을 확인하고 브라우저를 연다
#   ./start.sh --port 9000
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -x .venv/bin/python ]; then
  echo "가상환경을 만듭니다(.venv)."
  "$PY" -m venv .venv
fi
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  echo "패키지를 받습니다. 처음에는 몇 분 걸립니다."
  .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
  touch .venv/.installed
fi
exec .venv/bin/python tools/wuxi_web.py start "$@"
