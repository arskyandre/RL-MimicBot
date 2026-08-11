@echo off
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe "%~dp0replay_trimmer.py"
) else (
    start "" "%~dp0..\..\..\Python311\pythonw.exe" "%~dp0replay_trimmer.py"
)
