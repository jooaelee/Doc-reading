# 사내 Git 맞추기

> 이 저장소(GitHub `jooaelee/Doc-reading`)는 원래 저장소(`ljwon87ssu-cmyk/wuxi-ocr`)에서 2026-11-19 에 갈라진 판입니다. 사내 판을 어느 저장소로 맞출지 정하기 전에는 이 판으로 덮어쓰지 않습니다. 덮어쓰면 원래 저장소에만 있는 수정이 사내 판에서 사라집니다.

우시 판독의 원본은 GitHub 저장소입니다. 사내 Git `DT/KEROL_FMS_DOC_AI` 의 master 갈래에 있는 `wuxi_ocr/` 폴더는 이 저장소의 판을 그대로 받습니다.

- 우시 코드는 GitHub 에서만 고칩니다. 사내 `wuxi_ocr/` 를 직접 고치면 다음 맞추기 때 덮어써져 사라집니다.
- 맞추기는 `wuxi_ocr/` 폴더만 바꿉니다. docagent(`src/docai`)와 사내 저장소의 다른 파일은 건드리지 않습니다.
- 운영 값(규칙, 이력, 비밀번호, 올린 서류)은 `work/` 에 있고 Git 밖이라 맞추기와 상관없이 남습니다.
- 사내 이관 때 가렸던 실제 번호와 담당자 이름은 이 저장소에 그대로 있으므로, 맞추면 사내 판에도 다시 들어갑니다.

## 준비

| 무엇 | 어디에 |
|---|---|
| 사내망에 붙은 PC | 사내 Git 에 올릴 권한이 있는 계정(노트북 또는 .65) |
| Git Bash | Windows 는 Git for Windows 에 들어 있습니다. 아래 명령은 모두 Git Bash 에서 실행합니다 |
| 사내 저장소 사본 | 예: `C:/Users/SAMSUNG/KEROL_FMS_DOC_AI`. 어느 갈래에 있든, 고치던 것이 있든 그대로 둡니다 |
| 이 저장소 사본 | `git clone https://github.com/jooaelee/Doc-reading wuxi-ocr` |

## 1. 비교 (처음 한 번은 꼭)

사내 판에는 GitHub 로 옮기기 전에 사내에서만 고친 것이 남아 있을 수 있습니다. 덮어쓰면 그것이 사라지므로 먼저 비교합니다. 아무것도 바꾸지 않습니다.

```bash
cd wuxi-ocr
tools/sync_company_git.sh C:/Users/SAMSUNG/KEROL_FMS_DOC_AI --compare
```

세 묶음이 나옵니다.

| 묶음 | 볼 것 |
|---|---|
| 사내에만 있거나 내용이 다른 파일 | GitHub 판이 더 새로운 것인지, 사내에서만 고친 것인지 봅니다. 사내에서만 고친 것은 GitHub 에 먼저 옮기고 커밋합니다 |
| GitHub 에만 있는 파일 | 새로 들어갈 파일입니다(운영 화면, 규칙, 학습, 안내서). 볼 것이 없습니다 |
| 사내 작업 폴더에서 커밋하지 않은 변경 | 사내 원격에도 없는 수정입니다. 필요한 것은 GitHub 에 옮깁니다 |

사내 이관 때 한 수정(이관 커밋 `e63db149` 와 그 뒤 노트북 작업)은 작업 기록을 보고 이 저장소에 다시 얹었습니다(README 의 이관 기록). 다만 사내 판과 파일을 한 줄씩 대조하지는 못했으므로, 처음 맞추기 전에 이 비교로 확인합니다.

## 2. 맞추기 (커밋까지)

```bash
tools/sync_company_git.sh C:/Users/SAMSUNG/KEROL_FMS_DOC_AI
```

스크립트가 하는 일은 아래와 같습니다.

1. GitHub 에서 main 을 받고, 사내 원격에서 master 를 받습니다.
2. 사내 master 를 사내 저장소 옆의 임시 폴더(`KEROL_FMS_DOC_AI-wuxi-sync`)에 따로 꺼냅니다. 지금 쓰는 사내 작업 폴더는 그대로입니다.
3. 임시 폴더의 `wuxi_ocr/` 를 비우고 GitHub 판으로 채웁니다. GitHub 에 없는 파일은 지워지고, 사내에서만 추적하던 비밀번호 해시(`access.json`, `admin.json`)는 남깁니다.
4. `wuxi_ocr/` 밖의 파일이 하나라도 바뀌면 커밋하지 않고 멈춥니다.
5. 바뀌는 파일 목록을 보여 주고 커밋합니다. 커밋 메시지에 GitHub 커밋 주소가 들어갑니다.

다른 판을 맞추려면 `--ref <GitHub 커밋이나 갈래>`, 다른 사내 갈래로 보내려면 `--branch <갈래>` 를 붙입니다.

## 3. 올리기

스크립트가 마지막에 검토, 올리기, 치우기 명령을 보여 줍니다. 검토한 뒤 올립니다.

```bash
git -C C:/Users/SAMSUNG/KEROL_FMS_DOC_AI-wuxi-sync show --stat HEAD
```

```bash
git -C C:/Users/SAMSUNG/KEROL_FMS_DOC_AI-wuxi-sync push origin HEAD:master
```

```bash
git -C C:/Users/SAMSUNG/KEROL_FMS_DOC_AI worktree remove --force C:/Users/SAMSUNG/KEROL_FMS_DOC_AI-wuxi-sync
```

검토 없이 한 번에 올리려면 2단계에서 `--push` 를 붙입니다. 사내 원격이 그 사이에 바뀌어 올리기가 거절되면 임시 폴더를 치우고 2단계부터 다시 합니다.

## 4. 서버에 반영 (.65)

서버의 사내 저장소 사본에서 master 를 받습니다. `work/` 는 Git 밖이라 그대로 남습니다.

```bash
git -C <서버의 사내 저장소> pull origin master
```

처음 반영할 때는 아래를 한 번 합니다.

| 할 일 | 명령이나 방법 |
|---|---|
| 우시 패키지 | 따로 띄우면 `pip install -r wuxi_ocr/requirements.txt`. docagent 에 붙이면 처음 붙을 때 저절로 받습니다 |
| Claude 로그인 | 서버를 띄우는 운영체제 계정으로 `claude` 를 실행해 판독용 계정으로 로그인합니다 |
| 비밀번호 | `python wuxi_ocr/tools/wuxi_web.py set-password`, `set-admin-password` |
| 설치 점검 | `python wuxi_ocr/tools/make_sample.py` 다음 `python wuxi_ocr/tools/selfcheck.py` |

띄우는 방법은 둘 중 하나를 고릅니다.

| 방법 | 하는 법 | 언제 |
|---|---|---|
| 따로 띄우기(기본) | `python wuxi_ocr/tools/wuxi_web.py serve --host 0.0.0.0 --port 8767` 를 서비스로 등록합니다. docagent 코드는 한 줄도 바꾸지 않습니다 | 사내망에서 8767 포트를 열 수 있을 때 |
| docagent 에 붙이기 | docagent 서버 시작 코드에 아래 두 줄을 넣고, docagent 인증 미들웨어(`src/docai/webapp/access.py`)에서 `/wuxi` 로 시작하는 경로를 예외로 둡니다. 우시 앱은 자체 비밀번호를 씁니다 | 새 포트를 열 수 없을 때 |

```python
import sys; sys.path.insert(0, "<사내 저장소>/wuxi_ocr/tools")
import wuxi_mount; wuxi_mount.attach(app, "/wuxi")
```

붙인 뒤에는 고객 화면이 `/wuxi/`, 운영 화면이 `/wuxi/admin` 입니다. `WUXI_ENABLED=0` 을 주면 docagent 는 우시 없이 뜹니다.

반영한 뒤 운영 화면 「설정」에서 Claude 계정 이메일과 「시험 질문 보내기」를 확인하고, 고객 화면에서 가짜 묶음(`work/samples/sample/`)을 한 번 올립니다.

## 되돌리기

맞추기는 커밋 하나이므로 사내 저장소에서 그 커밋을 되돌리고 올립니다.

```bash
git -C <사내 저장소> revert <맞추기 커밋>
```
