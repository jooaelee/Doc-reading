"""다른 웹 서버(지금은 docagent)에 우시 앱을 붙이는 입구. 그 서버의 코드와 패키지 목록을 건드리지 않게 한다.

    import wuxi_mount; wuxi_mount.attach(app, "/wuxi")

- 우시가 쓰는 패키지(PDF 읽기 pymupdf, 스캔 OCR rapidocr)가 그 서버의 파이썬에 없으면, 처음 붙을 때 뒤에서 한 번
  WUXI_HOME/pydeps 에 따로 받는다(pip --target). 받는 동안 화면은 열리고 올리기만 「준비 중」으로 막힌다.
  받은 폴더는 sys.path 맨 뒤에 붙여, 그 서버가 이미 가진 패키지가 늘 먼저 쓰이게 한다(판이 섞이지 않게).
- WUXI_ENABLED=0 이면 붙이지 않는다.
"""
from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import wuxi_paths  # noqa: E402

log = logging.getLogger("wuxi")
NEEDED = ("pymupdf", "openpyxl")          # 없으면 판독을 못 한다
OPTIONAL = ("rapidocr_onnxruntime", "rapidocr")  # 하나만 있으면 된다. 없으면 스캔 쪽 가르기만 약해진다
_state = {"status": "unknown", "error": "", "since": 0.0}
_lock = threading.Lock()


def deps_dir() -> Path:
    return Path(wuxi_paths.WORK) / f"pydeps-py{sys.version_info.major}{sys.version_info.minor}"


def _add_path() -> None:
    d = str(deps_dir())
    if Path(d).is_dir() and d not in sys.path:
        sys.path.append(d)                 # 맨 뒤: 서버가 가진 패키지가 먼저
        importlib.invalidate_caches()


def _have(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


def ready() -> bool:
    _add_path()
    return all(_have(m) for m in NEEDED)


def status() -> dict:
    if _state["status"] != "installing" and ready():
        _state["status"] = "ready"
    return dict(_state)


def _install() -> None:
    req = Path(wuxi_paths.BASE) / "requirements.txt"
    target = deps_dir()
    target.mkdir(parents=True, exist_ok=True)
    logf = Path(wuxi_paths.WORK) / "pydeps.log"
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
           "--target", str(target), "-r", str(req)]
    try:
        with logf.open("a", encoding="utf-8") as fo:
            fo.write(f"\n== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)}\n")
            fo.flush()
            r = subprocess.run(cmd, stdout=fo, stderr=subprocess.STDOUT, timeout=1800)
        if r.returncode != 0 or not ready():
            raise RuntimeError(f"pip 끝 코드 {r.returncode}. 자세한 것은 {logf}")
        _state.update(status="ready", error="")
        log.info("우시 화면 패키지를 받았습니다: %s", target)
    except Exception as e:
        _state.update(status="failed", error=str(e)[:300])
        log.error("우시 화면 패키지를 받지 못했습니다: %s", e)


def ensure_deps(background: bool = True) -> None:
    """없는 패키지가 있으면 한 번 받는다. 이미 받는 중이거나 실패했으면 다시 시작하지 않는다(서버를 다시 띄우면 다시 해 봄)."""
    with _lock:
        if ready():
            _state.update(status="ready")
            return
        if _state["status"] in ("installing", "failed"):
            return
        _state.update(status="installing", since=time.time(), error="")
    if background:
        threading.Thread(target=_install, daemon=True, name="wuxi-pydeps").start()
    else:
        _install()


def attach(app, prefix: str = "/wuxi") -> bool:
    """app(FastAPI/Starlette)의 prefix 에 우시 앱을 붙인다. 붙였으면 True."""
    if (os.environ.get("WUXI_ENABLED") or "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    from starlette.responses import RedirectResponse

    import wuxi_web
    ensure_deps()
    app.add_api_route(prefix, lambda: RedirectResponse(prefix + "/"), include_in_schema=False)
    app.mount(prefix, wuxi_web.app)
    return True
