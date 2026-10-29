"""우시 OCR 폴더 안에서 모든 경로를 정하는 곳. 이 폴더만 떼어 가도 돌게 한다.

이관 전에는 개인 맥의 `~/Documents/kerol/...` 과 다른 저장소 폴더를 직접 가리켰다.
이제는 아래 세 곳만 쓴다. 모두 환경변수로 바꿀 수 있다.

  BASE    이 폴더(wuxi_ocr). 코드, 스키마, 문서
  WORK    작업 폴더. 기본 BASE/work. 서류 원본, 산출물, 캐시가 여기에 쌓이고 Git 에는 안 올라간다
  DATA    KEROL 기준 자료(선택). 기본 BASE/data. DB 파일은 Git 에 안 올린다
"""
import os
import shutil

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(BASE, "schema")
WORK = os.path.abspath(os.path.expanduser(os.environ.get("WUXI_HOME") or os.path.join(BASE, "work")))
DATA = os.path.abspath(os.path.expanduser(os.environ.get("WUXI_DATA") or os.path.join(BASE, "data")))


def work(*parts):
    """작업 폴더 아래 경로."""
    return os.path.join(WORK, *parts)


def doc_roots(env_value=None):
    """서류 원본 폴더 목록. 여러 개는 OS 경로 구분자(Windows `;`, 맥 리눅스 `:`)로 잇는다."""
    raw = env_value or os.environ.get("DOC_ROOTS")
    if not raw:
        return [work("docs_raw"), work("docs_raw_sea")]
    return [os.path.abspath(os.path.expanduser(p)) for p in raw.split(os.pathsep) if p.strip()]


def claude_bin():
    """Claude Code CLI 위치. WUXI_CLAUDE_BIN, CLAUDE_BIN, PATH, Windows 의 npm 설치 자리 순으로 찾는다.

    서버 프로세스(PM2 등)는 사람 터미널과 PATH 가 달라 이름만으로는 못 찾는 일이 있다. 그래서 흔한 자리를 더 본다.
    같은 서버에 docagent 가 있으면 그쪽이 정해 둔 위치(DOCAI_CLAUDE_CLI_PATH)도 빌려 본다(읽기만, 코드는 안 나눔).
    """
    for env in ("WUXI_CLAUDE_BIN", "CLAUDE_BIN", "DOCAI_CLAUDE_CLI_PATH"):
        v = os.environ.get(env)
        if v and os.path.exists(v) and not v.lower().endswith(".js"):
            return v
    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        for base in (os.environ.get("APPDATA"), os.path.expanduser("~/AppData/Roaming"), os.path.expanduser("~/.local/bin")):
            for name in ("npm/claude.cmd", "claude.exe", "claude.cmd"):
                cand = os.path.join(base or "", name)
                if base and os.path.exists(cand):
                    return cand
    return "claude"


def claude_config_dir():
    """Claude 계정 폴더. WUXI_CLAUDE_CONFIG_DIR, CLAUDE_CONFIG_DIR, 같은 서버 docagent 판독 계정 목록의 첫 계정 순.
    다 없으면 None(CLI 기본 계정)."""
    v = os.environ.get("WUXI_CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
    if v:
        return v
    dirs = [d.strip() for d in (os.environ.get("DOCAI_CLAUDE_CONFIG_DIRS") or "").split(os.pathsep) if d.strip()]
    return next((d for d in dirs if d.lower() != "default"), None)


class Upload:
    """cgi.FieldStorage 의 파일 칸 대신 쓰는 작은 꼴(filename, file)."""

    def __init__(self, filename, data):
        import io
        self.filename = filename
        self.file = io.BytesIO(data)


def upload_files(handler, field="pdf"):
    """multipart/form-data 요청에서 `field` 이름의 파일들을 꺼낸다.

    파이썬 3.13 에서 cgi 모듈이 없어져 표준 email 파서로 대신한다.
    """
    from email.parser import BytesParser
    from email.policy import HTTP

    ctype = handler.headers.get("Content-Type", "")
    size = int(handler.headers.get("Content-Length") or 0)
    body = handler.rfile.read(size) if size else b""
    if "multipart/form-data" not in ctype or not body:
        return []
    msg = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + ctype.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    out = []
    for part in msg.iter_parts():
        if part.get_param("name", header="content-disposition") != field:
            continue
        name = part.get_filename()
        if not name:
            continue
        out.append(Upload(name, part.get_payload(decode=True) or b""))
    return out
