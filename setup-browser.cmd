@echo off
cd /d "%~dp0"
for /f "usebackq tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"
python -c "import sys; sys.path[:0]=[r'%CD%\src',r'%CD%\.deps']; from chatgpt_web_provider.browser_setup import main; main()"
