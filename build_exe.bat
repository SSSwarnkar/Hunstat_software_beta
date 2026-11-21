:: build_exe.bat
@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo Building HunStat standalone Windows executable (HunStat.exe)
echo ============================================================

if not exist assets mkdir assets

:: Generate a valid 32x32 Windows .ico file using pure Python stdlib if not present
if not exist assets\hunstat.ico (
    echo Generating assets\hunstat.ico ...
    python -c "import struct, pathlib; w, h = 32, 32; px = bytearray(); [px.extend((180, 119, 31, 255) if (4 <= x < 28 and 4 <= y < 28 and (x in (8,9,22,23) or y in (15,16))) else (45, 30, 20, 255)) for y in range(h) for x in range(w)]; mask = bytes((w // 8) * h); bmp = struct.pack('<IiiHHIIiiII', 40, w, h*2, 1, 32, 0, len(px)+len(mask), 0, 0, 0, 0) + bytes(px) + mask; ico = struct.pack('<HHH', 0, 1, 1) + struct.pack('<BBBBHHII', w, h, 0, 0, 1, 32, len(bmp), 22) + bmp; pathlib.Path('assets/hunstat.ico').write_bytes(ico)"
)

pyinstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name HunStat ^
    --icon assets\hunstat.ico ^
    --hidden-import PySide6.QtCore ^
    --hidden-import PySide6.QtGui ^
    --hidden-import PySide6.QtWidgets ^
    --hidden-import PySide6.QtSvg ^
    --hidden-import PySide6.QtSvgWidgets ^
    --hidden-import PySide6.QtXml ^
    --hidden-import pyqtgraph ^
    --hidden-import pyqtgraph.exporters ^
    --hidden-import pyqtgraph.exporters.ImageExporter ^
    --hidden-import pyqtgraph.exporters.SVGExporter ^
    --hidden-import scipy.signal ^
    --hidden-import serial ^
    --hidden-import serial.tools.list_ports ^
    --hidden-import hunstat.analysis ^
    --hidden-import hunstat.gui_analysis ^
    --hidden-import hunstat.dummy_wizard ^
    main.py

if %ERRORLEVEL% neq 0 (
    echo [ERROR] PyInstaller build failed with exit code %ERRORLEVEL%.
    exit /b %ERRORLEVEL%
)

echo ============================================================
echo Build succeeded: dist\HunStat.exe
echo ============================================================
endlocal