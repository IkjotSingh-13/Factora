@echo off
REM One-shot Windows setup fallback for cmd.exe.
REM Use this if PowerShell blocks script execution or a .ps1 will not parse.
REM ASCII only + CRLF line endings on purpose (batch files are picky about both).
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 goto nopython

echo.
echo == 1. Virtual environment ==
if not exist .venv (
    python -m venv .venv
    if errorlevel 1 goto failed
    echo    created .venv
) else (
    echo    already exists
)

echo.
echo == 2. Install dependencies ==
.venv\Scripts\python.exe -c "import sys; print('   interpreter:', sys.executable)"
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
if exist requirements-s2.txt (
    echo    using requirements-s2.txt ^(module-scoped, cannot be overwritten by teammates^)
    .venv\Scripts\python.exe -m pip install --quiet -r requirements-s2.txt
) else (
    echo    requirements-s2.txt missing, falling back to requirements.txt
    .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
)
if errorlevel 1 goto failed

REM Verify the packages are importable by THIS interpreter - printing
REM "installed" without checking is how a missing pydantic reaches the demo.
.venv\Scripts\python.exe -c "import requests, pydantic, pytest; print('   verified:', 'requests', requests.__version__, '| pydantic', pydantic.VERSION, '| pytest', pytest.__version__)"
if errorlevel 1 (
    echo.
    echo    ERROR: dependencies are not importable by .venv\Scripts\python.exe
    echo    Run this to see why:
    echo      .venv\Scripts\python.exe -m pip install pydantic requests pytest
    echo    Then diagnose with:
    echo      .venv\Scripts\python.exe scripts\doctor.py
    goto failed
)

echo.
echo == 3. .env ==
if not exist .env (
    copy /y .env.example .env >nul
    echo    created .env from .env.example
) else (
    echo    .env already present
)

echo.
echo == 4. Tests offline ==
.venv\Scripts\python.exe -m pytest -q
if errorlevel 1 goto failed

echo.
echo == 5. Offline smoke run ==
.venv\Scripts\python.exe s2_retrieval.py --offline "vitamin C prevents the common cold" --top-k 3

echo.
echo == 6. Environment doctor ==
.venv\Scripts\python.exe scripts\doctor.py

echo.
echo Done.
echo   activate:        .venv\Scripts\activate.bat
echo   live retrieval:  python s2_retrieval.py "The Eiffel Tower is 330 metres tall."
echo   before pushing:  scripts\pre_pr_check.ps1  (or run the commands manually)
echo.
exit /b 0

:nopython
echo.
echo python not found on PATH.
echo Install Python 3.10+ from python.org, tick "Add python.exe to PATH", reopen the terminal.
exit /b 1

:failed
echo.
echo Setup failed. Scroll up for the error.
exit /b 1
