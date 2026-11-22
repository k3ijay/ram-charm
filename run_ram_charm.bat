@echo off
setlocal
cd /d "%~dp0"

set "SCRIPT=%~dp0ram_charm.py"

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%SCRIPT%"
    exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%SCRIPT%"
    exit /b 0
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw "%SCRIPT%"
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "" /min python "%SCRIPT%"
    exit /b 0
)

mshta "javascript:alert('Python was not found. Please install Python 3 and try again.');close();"
exit /b 1
