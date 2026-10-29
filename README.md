# 하이닉스 우시 서류 판독(G-ERP 품목 표)

이 폴더만 따로 떼어 가도 돕니다. 회사 docagent(`src/docai`)와 코드를 나눠 쓰지 않습니다.

## 하는 일

공급사가 보낸 서류 묶음(인보이스, 포장명세서, 중국 통관 용도설명서, 운송장)을 읽습니다. 품목 줄마다 품명, PO, PO 품목 번호, SAP 코드, 수량, 단가, 금액, 원산지를 뽑아 G-ERP 「B/L Item Update」 열 순서의 엑셀로 냅니다.

| 단계 | 하는 일 | 코드 |
|---|---|---|
| 1. 쪽 가르기 | 쪽마다 운송장, 인보이스, 포장명세서, 통관표, 약관을 가름. 스캔 쪽은 OCR 로 먼저 읽고 가름 | `docpipe.classify`, `ocr_pages` |
| 2. 판독 | 서류별로 AI(Claude)에게 보내고, 정해진 꼴(스키마)로만 답을 받음 | `docpipe.run`, `extract_mcp.py` |
| 3. 후처리 | 통관표의 SAP 코드와 원산지를 인보이스 줄에 채움, 원문에 없는 코드 지움, 겹친 줄 뺌 | `hynix_merge_cn`, `hynix_drop_unfounded` |
| 4. 검증 | 인보이스마다 줄 합계와 총액 대조 | `docpipe.verify` |
| 5. 엑셀 | 인보이스 줄에 포장명세서 줄(중량, 케이스)을 이어 G-ERP 표로 | `gerp_xlsx.py` |
| 6. 화면 | 서류를 올리고, 원본 위치를 보며 고치고, 엑셀을 받음 | `hynix_demo.py` (8766) |

## 설치

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt -r requirements-dev.txt
```

- 파이썬 3.11 이상. Windows, 맥, 리눅스 모두 됨(3.13 에서 확인).
- AI 는 Claude Code CLI(`claude`)를 부릅니다. PATH 에 있어야 하고, 다른 곳이면 `CLAUDE_BIN` 으로 줍니다. 계정 폴더는 `CLAUDE_CONFIG_DIR`.
- 스캔 쪽 OCR 은 RapidOCR(requirements 에 있음). 맥은 `swiftc -O tools/ocr_vision.swift -o bin/ocr_vision` 로 만들면 Apple Vision 을 씁니다.

## 실행

| 하려는 일 | 명령 |
|---|---|
| 설치 점검(가짜 서류로 실제 판독) | `python tools/make_sample.py` 다음 `python tools/selfcheck.py`, 스캔은 `--scan` |
| 화면 | `python tools/hynix_demo.py` 후 http://127.0.0.1:8766 |
| 명령줄 | `python tools/docpipe.py --bundle 이름=a.pdf,b.pdf --prompt h1 --claude-cli --codes schema/codes.json --save-full out.json` 다음 `python tools/gerp_xlsx.py --pred out.json --out out.xlsx` |
| 시험 | `python -m pytest tests` (AI 부르지 않음) |

## 폴더

| 폴더 | 내용 | Git |
|---|---|---|
| `tools/` | 엔진, 화면, 엑셀. 경로는 모두 `wuxi_paths.py` 한 곳에서 정함 | 올림 |
| `schema/` | KEROL 코드 목록, 장소 표, 단위 별칭 | 올림 |
| `tests/` | 시험 205개(값은 모두 지어낸 것) | 올림 |
| `docs/` | 하이닉스 중국법인 판 설명, G-ERP PO 매핑, 확인 화면 설계 | 올림 |
| `work/` | 서류 원본, 결과, 캐시(`WUXI_HOME` 으로 바꿈) | 안 올림 |
| `data/` | KEROL 기준 DB(선택, 장소 표를 다시 만들 때만, `WUXI_DATA`) | 안 올림 |

## 환경변수

| 이름 | 기본값 | 뜻 |
|---|---|---|
| `WUXI_HOME` | `work/` | 작업 폴더 |
| `DOC_ROOTS` | `work/docs_raw`, `work/docs_raw_sea` | 화면의 표본 서류 폴더. 여럿은 OS 경로 구분자(Windows `;`) |
| `CLAUDE_BIN`, `CLAUDE_CONFIG_DIR`, `MODEL` | PATH 의 claude, CLI 기본 계정, claude-sonnet-5 | AI 호출 |
| `DOCPIPE_OCR_ENGINE` | 자동(맥 Vision, 아니면 RapidOCR) | OCR 엔진 |

## 이관 기록

- 2026-10-29: 이 저장소(GitHub `commet/wuxi-ocr`)를 우시 판의 원본으로 삼았습니다. 사내 Git 의 `wuxi_ocr/` 는 이 저장소의 main 으로 덮어씁니다.
- 바탕은 개인 저장소 fsk-ops main 의 우시 파일(실제 번호와 담당자 이름이 든 원본)이고, 같은 날 사내 이관 때 한 작업을 그대로 다시 얹었습니다.
  - 경로를 `wuxi_paths.py` 한 곳으로 모음, 파이썬 3.13 에서 없어진 `cgi` 대체, Windows 에서 `claude.cmd` 찾기와 utf-8 출력
  - docagent 에서 합친 규칙 두 가지(통관표 품명 앞부분이 여러 줄에 걸리면 SAP 코드를 고르지 않기, 해상 품명 상용구의 수량 낱말)
  - 포장명세서에 코드 열이나 줄별 인보이스 번호가 없으면 엑셀 품목 줄이 두 배가 되던 것 수정
  - 확인 화면과 진단 화면의 HTML, JSON 탈출 보강
  - 새 고객 화면(`wuxi_web.py`), 빠짐 검사(`wuxi_engine.py`), 다른 서버에 붙이는 입구(`wuxi_mount.py`), 설치 점검과 일괄 점검 도구
- 사내 이관 때 했던 실제 번호와 이름 가리기는 하지 않았습니다. 이 저장소는 비공개이고, 규칙과 시험이 실제 서류 기준으로 돌아야 하기 때문입니다.
- RapidOCR 은 파이썬 3.12 까지 `rapidocr_onnxruntime`, 3.13 부터 `rapidocr` 3.x 를 씁니다(엔진이 둘 다 받습니다).
- 확인: 시험 205개 중 204개 통과(1개는 KEROL DB 파일이 있어야 돎). 가짜 묶음 실제 판독은 품목 27칸 모두 맞음, 엑셀 품목 3줄.
