# 하이닉스 우시 서류 판독 (Wuxi OCR)

하이닉스 우시 담당자가 공급사 서류 묶음(인보이스, 포장명세서, 중국 통관 용도설명서, 운송장)을 올리면, BL 한 장마다 품목 줄을 읽어 G-ERP 「B/L Item Update」 입력용 엑셀로 만드는 웹 앱입니다. 운영 화면에서 규칙을 고치고, 사람이 고친 값으로 규칙을 학습합니다.

가장 중요한 기준은 **품목 줄이 하나도 빠지면 안 된다**는 것입니다. 그래서 AI 답과 상관없는 근거로 줄을 다시 세는 빠짐 검사를 따로 둡니다.

> **넘겨받은 분은** 인수인계서 [docs/handover/Wuxi_OCR_인수인계서_v1.0.0.pdf](docs/handover/Wuxi_OCR_인수인계서_v1.0.0.pdf) 부터 읽습니다(1부 사람이 읽는 운영 안내, 2부 AI 조수 지침). 지금 어디까지 됐는지는 [docs/진행-상황.md](docs/진행-상황.md).

> **갈라진 판.** 이 저장소(GitHub `jooaelee/Doc-reading`)는 2026-11-19 에 `ljwon87ssu-cmyk/wuxi-ocr` 의 d79e1c8 판에서 갈라져 따로 고칩니다. 원래 저장소에 그 뒤 들어간 수정은 들어 있지 않고, 이 저장소의 수정도 원래 저장소에 저절로 가지 않습니다.

## 5분 시작 (자기 Claude 계정으로)

1. 파이썬 3.11 이상과 Claude Code 를 설치합니다.
   - Windows(PowerShell): `irm https://claude.ai/install.ps1 | iex`
   - 맥, 리눅스: `curl -fsSL https://claude.ai/install.sh | bash`
2. 터미널에서 `claude` 를 실행해 자기 계정으로 로그인하고 끝냅니다. 판독은 이 계정으로 돕니다.
3. 저장소를 받습니다: `git clone https://github.com/jooaelee/Doc-reading wuxi-ocr`
4. 띄웁니다. Windows 는 `start.bat` 을 두 번 누르고, 맥과 리눅스는 `./start.sh` 를 실행합니다.
   처음에는 가상환경과 패키지를 만들고, 운영 화면과 고객 화면 비밀번호를 묻고, Claude 로그인을 확인한 뒤 브라우저를 엽니다.
5. 고객 화면은 http://127.0.0.1:8767/ , 운영 화면은 http://127.0.0.1:8767/admin 입니다.

설치가 잘 되었는지는 가짜 서류로 확인합니다: `python tools/make_sample.py` 다음 `python tools/selfcheck.py`. 품목 27칸이 모두 맞으면 정상입니다.

## 화면 두 개

| 화면 | 쓰는 사람 | 하는 일 |
|---|---|---|
| 고객 화면 `/` | 하이닉스 우시 담당자 | 서류를 올리고, 진행을 보고, 표를 원본과 대조하며 고치고(줄 추가와 삭제 포함), 엑셀을 받고, 확정합니다. 빠진 줄이 의심되면 맨 위에 알리고 사람이 판정합니다 |
| 운영 화면 `/admin` | FSK 운영 담당 | 현황(들어온 묶음과 처리 기록), 규칙, 학습, 이력, 설정(Claude 계정, 모델, 보관 기간, 비밀번호) |

자세한 쓰는 법은 [docs/운영-화면.md](docs/운영-화면.md) 에 있습니다.

## 하는 일

| 단계 | 하는 일 | 코드 |
|---|---|---|
| 1. 쪽 가르기 | 쪽마다 운송장, 인보이스, 포장명세서, 통관표, 약관을 가릅니다. 스캔 쪽은 OCR 로 먼저 읽습니다 | `docpipe.classify`, `ocr_pages` |
| 2. 공급사 알아보기 | 앞쪽 글이나 송하인 이름으로 공급사를 정합니다. 처음 보는 송하인은 공급사로 새로 적습니다 | `wuxi_rules.supplier_for` |
| 3. AI 판독 | 서류별로 Claude 에게 보내 정해진 꼴(스키마)로만 답을 받습니다. 운영 규칙의 열 이름과 지침이 프롬프트에 더해집니다 | `docpipe.run`, `wuxi_rules.prompt_addendum` |
| 4. 후처리 | 통관표의 SAP 코드와 원산지를 인보이스 줄에 채우고, 운영 규칙(코드 사전, 값 바꾸기)을 적용합니다 | `hynix_merge_cn`, `wuxi_rules.apply_post` |
| 5. 검증 | 인보이스마다 줄 합계와 총액을 대조합니다 | `docpipe.verify` |
| 6. 빠짐 검사 | 금액 합계, 포장명세서 줄 수, 통관표 줄 수, 쪽마다 읽은 줄의 네 가지로 다시 셉니다 | `wuxi_engine.completeness` |
| 7. 엑셀 | 인보이스 줄에 포장명세서 줄(중량, 케이스)을 이어 G-ERP 표로 냅니다 | `gerp_xlsx.py` |

## 규칙과 학습

- 규칙 종류는 코드 사전, 값 바꾸기, 열 이름, 판독 지침, 빠짐 검사, 필수 칸의 여섯 가지입니다. 범위는 공통 또는 공급사 하나이고, 같은 자리에서는 공급사 규칙이 이깁니다.
- 단계는 후보, 반영, 격상(공급사 규칙을 공통으로 올림)이고, 끄거나 거절할 수 있습니다. 판독할 때마다 어떤 규칙이 어느 줄에 쓰였는지 기록해 현황과 규칙 화면에 적용 건수로 보입니다.
- 학습 신호는 확인 화면에서 고친 칸, 더한 줄, 「빠진 줄 의심」 판정, 확정 네 가지입니다. 같은 수정이 한 번만 있어도 후보가 생기지만, 반영은 사람이 근거와 미리보기를 보고 정합니다.
- 저장: 기본값은 `schema/rules_default.json`(Git), 운영 값은 `WUXI_HOME/rules.json`, 이력은 `WUXI_HOME/rules_history.jsonl` 입니다. 운영 값을 저장소 기본값으로 올리려면 `python tools/wuxi_rules.py export --to-default` 를 실행하고 커밋합니다.

## 명령

| 하려는 일 | 명령 |
|---|---|
| 띄우기(처음 쓰는 사람) | `start.bat` 또는 `./start.sh` (속으로 `python tools/wuxi_web.py start`) |
| 띄우기(서버) | `python tools/wuxi_web.py serve --host 0.0.0.0 --port 8767` |
| 비밀번호 | `python tools/wuxi_web.py set-password`, `python tools/wuxi_web.py set-admin-password` |
| 설치 점검 | `python tools/make_sample.py` 다음 `python tools/selfcheck.py` (스캔은 `--scan`) |
| 긴 묶음 빠짐 시험 | `python tools/make_sample.py --lines 120 --invoices 3 --name long` 다음 `python tools/selfcheck.py --name long` |
| 실제 서류 일괄 점검 | `python tools/batch_check.py work/docs_raw/hx --limit 10` (이어 하려면 `--resume`) |
| 나눠 읽기 | 서버를 끈 채 `python tools/bulk_jobs.py <폴더> --limit 50` 으로 50건씩 넣고 서버를 켬. 건별 폴더와 한곳에 모인 PDF 를 모두 받음. 이미 넣은 건은 건너뛰므로 같은 명령을 다시 돌리면 다음 50건 |
| 정확도 재기 | `python tools/accuracy.py` (확정한 정답과 판독을 견줌. `--save 이름` 으로 남기고, 고쳐 다시 읽은 뒤 `--compare 이름` 으로 견줌. `--cases 건 --detail` 로 틀린 칸과 남는 줄) |
| 규칙 정답 시뮬레이션 | 운영 화면 「학습」 탭에 후보마다 「반영하면」, 「공통으로 반영하면」, 맨 위에 「후보를 모두 반영하면」 이 나옴(확정한 정답의 Main 칸 정확도 전과 후, 바로잡는 칸, 틀어지는 칸, 틀린 값으로 채우는 칸). 명령줄은 `python tools/rule_sim.py`. 열 이름과 판독 지침은 반영한 뒤 다시 읽어 `accuracy.py --compare` 로 견줌 |
| 학습 후보 다시 만들기 | `python tools/learn_replay.py` 로 미리 보고 `--apply` 로 고침(후보 만드는 기준이 바뀐 뒤, 확정한 정답으로 후보를 새로 만듦. 반영한 규칙은 그대로) |
| 읽은 작업 맞추기 | 서버를 끈 채 `python tools/jobs_backfill.py` (다시 읽지 않고 통화를 채우고 못 이은 포장명세서 줄을 수량으로 이음. `--dry-run` 으로 미리 봄) |
| 정답 확정용 일괄 등록 | 서버를 끈 채 `python tools/bulk_jobs.py work/docs_raw/hx --order <일괄 점검 결과 JSON>` 다음 서버를 켬. 건마다 대기 작업이 되어 차례로 읽히고, 고객 화면 목록에서 열어 확정함(`--dry-run` 으로 미리 봄). 보관 기간을 먼저 늘림 |
| 규칙 보기, 옮기기 | `python tools/wuxi_rules.py list`, `export`, `import <파일>` |
| 자동 시험 | `python -m pytest tests` (AI 를 부르지 않음. GitHub Actions 설정(`.github/workflows/test.yml`)이 있어, 계정에서 Actions 를 쓸 수 있으면 push 마다 돎) |

## 폴더

| 폴더 | 내용 | Git |
|---|---|---|
| `tools/` | 엔진, 화면 서버, 규칙, 학습, 엑셀. 경로는 `wuxi_paths.py` 한 곳에서 정합니다 | 올림 |
| `web/` | 고객 화면(`index.html`), 운영 화면(`admin.html`), 공통 디자인과 pdf.js(`static/`) | 올림 |
| `schema/` | KEROL 코드 목록, 장소 표, 단위 별칭, 규칙 기본값 | 올림 |
| `tests/` | 자동 시험 | 올림 |
| `docs/` | 인수인계서(`handover/`), 진행 상황, 운영 화면 안내, 사내 Git 맞추기, 하이닉스 중국법인 판 설명, G-ERP PO 매핑, 확인 화면 설계 | 올림 |
| `work/` | 서류 원본, 작업, 결과, 규칙 운영 값, 이력, 비밀번호 해시(`WUXI_HOME` 으로 바꿈) | 안 올림 |
| `data/` | KEROL 기준 DB(선택, 장소 표를 다시 만들 때만, `WUXI_DATA`) | 안 올림 |

## 설정

운영 화면 「설정」에서 바꾸면 `WUXI_HOME/settings.json` 에 남고 환경변수보다 먼저 쓰입니다.

| 이름 | 기본값 | 뜻 |
|---|---|---|
| `WUXI_HOME` | `work/` | 작업 폴더 |
| `MODEL` | `claude-sonnet-5` | 판독 모델 |
| `WUXI_KEEP_DAYS` | 14 | 올린 서류와 결과의 보관 일수 |
| `CLAUDE_CONFIG_DIR`, `WUXI_CLAUDE_CONFIG_DIR` | 없음(기본 계정) | 판독에 쓸 Claude 계정 폴더 |
| `CLAUDE_BIN`, `WUXI_CLAUDE_BIN` | PATH 의 `claude` | Claude Code CLI 위치 |
| `WUXI_PASSWORD_HASH`, `WUXI_ADMIN_HASH` | 없음 | 비밀번호 해시(JSON). 없으면 `WUXI_HOME/access.json`, `admin.json`, 저장소의 같은 파일 순 |
| `WUXI_SESSION_SECRET` | 없음 | 로그인 쿠키 서명 열쇠. 없으면 `WUXI_HOME/.secret` 을 주인만 읽게 만들어 씀 |
| `WUXI_ENABLED` | 1 | 0 이면 다른 서버에 붙지 않음 |
| `DOCPIPE_OCR_ENGINE` | 자동 | `rapid`, `vision`(맥), `none` |

## 다른 서버에 붙이기 (/wuxi)

docagent 처럼 FastAPI 서버가 이미 있으면 그 서버의 코드는 거의 건드리지 않고 같은 포트의 `/wuxi` 로 붙입니다.

```python
import sys; sys.path.insert(0, "<저장소>/wuxi_ocr/tools")
import wuxi_mount; wuxi_mount.attach(app, "/wuxi")
```

우시 전용 패키지(PDF 읽기, OCR)는 그 서버의 파이썬에 없으면 처음 붙을 때 `WUXI_HOME/pydeps` 에 따로 받고, 서버 패키지와 섞지 않습니다. 그 서버에 인증 미들웨어가 있으면 `/wuxi` 를 예외로 둡니다(우시 앱이 자체 비밀번호를 씁니다).

## 사내 Git 맞추기

사내 Git(`DT/KEROL_FMS_DOC_AI`)의 `wuxi_ocr/` 폴더는 `tools/sync_company_git.sh` 로 GitHub 저장소 판으로 덮어씁니다. 이 저장소(`jooaelee/Doc-reading`)는 원래 저장소(`ljwon87ssu-cmyk/wuxi-ocr`)에서 갈라진 판이므로, 사내 판을 어느 저장소로 맞출지 정하기 전에는 이 판으로 덮어쓰지 않습니다. 덮어쓰면 원래 저장소에만 있는 수정이 사내 판에서 사라집니다. 순서는 [docs/사내-Git-맞추기.md](docs/사내-Git-맞추기.md) 에 있습니다.

## 이관 기록

- 2026-10-29: 이 저장소를 우시 판의 원본으로 삼았습니다. 바탕은 개인 저장소 fsk-ops main 의 우시 파일(실제 번호와 담당자 이름이 든 원본)이고, 같은 날 사내 이관 때 한 작업을 다시 얹었습니다.
  - 경로를 `wuxi_paths.py` 한 곳으로 모음, 파이썬 3.13 에서 없어진 `cgi` 대체, Windows 에서 `claude.cmd` 찾기와 utf-8 출력
  - docagent 에서 합친 규칙 두 가지(통관표 품명 앞부분이 여러 줄에 걸리면 SAP 코드를 고르지 않기, 해상 품명 상용구의 수량 낱말)
  - 포장명세서에 코드 열이나 줄별 인보이스 번호가 없으면 엑셀 품목 줄이 두 배가 되던 것 수정
  - 확인 화면과 진단 화면의 HTML, JSON 탈출 보강
  - 새 고객 화면, 빠짐 검사, 다른 서버에 붙이는 입구, 설치 점검과 일괄 점검 도구
- 사내 이관 때 했던 실제 번호와 이름 가리기는 하지 않았습니다. 비공개 저장소이고, 규칙과 시험이 실제 서류 기준으로 돌아야 하기 때문입니다.
- 같은 날 운영 화면(현황, 규칙, 학습, 이력, 설정)과 규칙 저장소, 학습을 더했습니다.
- RapidOCR 은 파이썬 3.12 까지 `rapidocr_onnxruntime`, 3.13 부터 `rapidocr` 3.x 를 씁니다(엔진이 둘 다 받습니다).
- 확인: 가짜 묶음 실제 판독 품목 27칸 모두 맞음(글자 12초, 스캔 28초), 120줄 세 장짜리 긴 묶음 1080칸 모두 맞음(107초).
