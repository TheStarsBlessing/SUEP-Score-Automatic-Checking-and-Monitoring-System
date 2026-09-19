@echo off
chcp 65001 >nul
setlocal
rem ---------------------------------------------------------------------------
rem 重新打包桌面版 exe
rem 依赖：pip install pyinstaller requests lxml pysocks
rem 产物：本目录下的「SUEP成绩监控.exe」
rem ---------------------------------------------------------------------------
cd /d "%~dp0"

python -m PyInstaller grade_gui.py ^
  --name "SUEP成绩监控" ^
  --onefile --windowed ^
  --icon icon.ico ^
  --hidden-import socks ^
  --exclude-module numpy --exclude-module matplotlib --exclude-module pandas ^
  --exclude-module scipy --exclude-module PyQt5 --exclude-module PyQt6 ^
  --exclude-module PySide2 --exclude-module PySide6 --exclude-module IPython ^
  --exclude-module notebook --exclude-module pytest ^
  --noconfirm --clean ^
  --workpath build --distpath . --specpath build

if errorlevel 1 (
  echo.
  echo 打包失败，请检查上面的输出。
  pause
  exit /b 1
)

echo.
echo 打包完成：%~dp0SUEP成绩监控.exe
echo.
echo 自检（无界面，结果写入 selftest_result.txt）：
echo   SUEP成绩监控.exe --selftest
pause
