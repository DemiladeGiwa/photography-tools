#!/usr/bin/env python3
"""Build Photography Tools into something you can hand to people who don't have Python.

    python scripts/build_standalone.py            # dist/PhotographyTools/ + a .zip (+ Setup.exe if Inno Setup is installed)
    python scripts/build_standalone.py --onefile  # a single PhotographyTools.exe (slower to start)
    python scripts/build_standalone.py --debug    # keep a console window to see errors

On Windows, just double-click build.bat in the project folder instead.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from photo_sorter import __version__  # noqa: E402

APP = "PhotographyTools"
ICON = ROOT / "photo_sorter" / "gui" / "assets" / "icon.ico"


def _version_file(build_dir: Path) -> Path:
    """Windows file properties (shown in Explorer > Properties > Details)."""
    nums = (list(map(int, __version__.split("."))) + [0, 0, 0, 0])[:4]
    tup = ", ".join(map(str, nums))
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Demi'),
      StringStruct('FileDescription', 'Photography Tools'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{APP}'),
      StringStruct('LegalCopyright', 'MIT licence'),
      StringStruct('OriginalFilename', '{APP}.exe'),
      StringStruct('ProductName', 'Photography Tools'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    build_dir.mkdir(parents=True, exist_ok=True)
    p = build_dir / "version_info.txt"
    p.write_text(text, encoding="utf-8")
    return p


def find_iscc() -> str:
    for cand in (shutil.which("ISCC"), shutil.which("iscc"),
                 r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe",
                 # winget can install it per user
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe")):
        if cand and Path(cand).exists():
            return str(cand)
    return ""


UPDATE_BAT = r"""@echo off
setlocal
rem Updates an existing Photography Tools folder in place. Your settings, recent folders,
rem analysis caches and decisions are stored elsewhere and are not touched.
set "SRC=%~dp0PhotographyTools"
if not exist "%SRC%\PhotographyTools.exe" (echo Run this from the unzipped update folder. & pause & exit /b 1)
set "DEST=%~1"
if "%DEST%"=="" set /p DEST=Folder where your current PhotographyTools.exe lives:
if not exist "%DEST%\PhotographyTools.exe" (echo Couldn't find PhotographyTools.exe in "%DEST%". & pause & exit /b 1)
tasklist /fi "imagename eq PhotographyTools.exe" | find /i "PhotographyTools.exe" >nul && (echo Please close Photography Tools first. & pause & exit /b 1)
robocopy "%SRC%" "%DEST%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (echo Update failed. & pause & exit /b 1)
echo Updated to __VERSION__. Open Photography Tools and carry on where you left off.
pause
"""


def make_update_zip(folder: Path, dist: Path) -> None:
    """Zip for portable copies: unzip anywhere, run Update-PhotographyTools.bat, point it at the old folder."""
    path = dist / f"{APP}-{__version__}-update.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in folder.rglob("*"):
            z.write(f, Path(APP) / f.relative_to(folder))
        z.writestr("Update-PhotographyTools.bat", UPDATE_BAT.replace("__VERSION__", __version__).replace("\n", "\r\n"))
    print(f"Update zip:    {path}")


def build(onefile: bool = False, debug: bool = False, installer: bool = True) -> None:
    if importlib.util.find_spec("PyInstaller") is None:
        print("Installing PyInstaller…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    sep = ";" if os.name == "nt" else ":"
    cmd = [sys.executable, "-m", "PyInstaller", f"--name={APP}", "--noconfirm", "--clean",
           f"--paths={ROOT}", "--collect-submodules=photo_sorter", "--exclude-module=photo_sorter.tests",
           "--exclude-module=pytest", "--hidden-import=PIL._tkinter_finder",
           f"--add-data={ROOT / 'photo_sorter' / 'gui' / 'assets'}{sep}photo_sorter/gui/assets"]
    if ICON.exists():
        cmd.append(f"--icon={ICON}")
    if os.name == "nt":
        cmd.append(f"--version-file={_version_file(ROOT / 'build')}")
    if not debug:
        cmd.append("--windowed")
    for optional in ("rawpy", "exifread", "send2trash", "windnd", "pillow_heif"):
        if importlib.util.find_spec(optional) is not None:
            cmd.append(f"--collect-all={optional}" if optional in ("rawpy", "pillow_heif") else f"--hidden-import={optional}")
        elif optional == "rawpy":
            print("WARNING: rawpy isn't installed - the build won't read RAW files on their own.")
    cmd.append("--onefile" if onefile else "--onedir")
    cmd.append(str(ROOT / "Photography Tools.pyw"))

    print(" ".join(cmd))
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode:
        sys.exit(res.returncode)

    dist = ROOT / "dist"
    if onefile:
        print(f"\nDone: {dist / (APP + ('.exe' if os.name == 'nt' else ''))}")
        return
    folder = dist / APP
    for extra in ("README.md", "LICENSE"):
        if (ROOT / extra).exists():
            shutil.copy2(ROOT / extra, folder / extra)
    plat = "win64" if os.name == "nt" else sys.platform
    zip_path = dist / f"{APP}-{__version__}-{plat}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in folder.rglob("*"):
            z.write(f, Path(APP) / f.relative_to(folder))
    print(f"\nPortable app: {folder}\nZip to share:  {zip_path}")
    make_update_zip(folder, dist)

    if installer and os.name == "nt":
        iscc = find_iscc()
        if iscc:
            subprocess.check_call([iscc, f"/DAppVersion={__version__}", str(ROOT / "installer" / "PhotographyTools.iss")])
            print(f"Installer:     {dist / f'{APP}-{__version__}-Setup.exe'}")
        else:
            print("Tip: install Inno Setup 6 (free, jrsoftware.org) and run this again to also get a Setup.exe.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--onefile", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-installer", action="store_true")
    a = ap.parse_args()
    build(a.onefile, a.debug, not a.no_installer)
