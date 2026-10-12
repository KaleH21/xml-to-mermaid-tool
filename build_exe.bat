@echo off
setlocal

pushd "%~dp0"

python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [build] Installing PyInstaller...
    python -m pip install -r requirements-build.txt
    if errorlevel 1 goto :error
)

echo [build] Creating dist\xml-to-mermaid.exe...
python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name xml-to-mermaid ^
    --distpath "%CD%\dist" ^
    --workpath "%CD%\build\pyinstaller" ^
    --specpath "%CD%\build" ^
    "%CD%\xml_to_mermaid_gui.py"

if errorlevel 1 goto :error

echo.
echo [build] Done: %CD%\dist\xml-to-mermaid.exe
popd
exit /b 0

:error
echo.
echo [build] Build failed.
popd
exit /b 1
