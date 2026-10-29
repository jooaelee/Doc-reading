@echo off
rem 우시 서류 판독을 띄운다(Windows). 처음에도, 두 번째부터도 이 파일을 두 번 누르면 된다.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if exist ".venv\Scripts\python.exe" goto :pkgs
echo 가상환경을 만듭니다.
py -3 -m venv .venv 2>nul || python -m venv .venv || goto :nopy
:pkgs
if exist ".venv\.installed" goto :run
echo 패키지를 받습니다. 처음에는 몇 분 걸립니다.
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || goto :fail
echo ok> ".venv\.installed"
:run
".venv\Scripts\python.exe" tools\wuxi_web.py start %*
pause
goto :eof
:nopy
echo 파이썬을 찾지 못했습니다. python.org 에서 3.11 이상을 설치한 뒤 다시 실행하십시오.
pause
goto :eof
:fail
echo 패키지를 받지 못했습니다. 인터넷 연결이나 프록시 설정을 확인하십시오.
pause
