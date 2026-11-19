"""Đóng gói thành .exe bằng PyInstaller.

    python packaging/build.py            # build cả GUI và CLI
    python packaging/build.py gui        # chỉ GUI

Kết quả nằm trong dist/. File .exe vẫn đọc `data/` cạnh nó, nên copy nguyên
thư mục data (config, session, answers) ra cạnh file chạy.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMON = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
          "--name", "IOE-Auto"]

TARGETS = {
    "gui": [os.path.join(ROOT, "run.py"), "gui"] + COMMON[:4] + [
        "--noconsole", "--name", "IOE-Auto", os.path.join(ROOT, "run.py")],
}


def _command(target: str) -> list[str]:
    base = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
            "--distpath", os.path.join(ROOT, "dist")]
    entry = os.path.join(ROOT, "run.py")
    if target == "gui":
        return base + ["--noconsole", "--name", "IOE-Auto-GUI", entry]
    return base + ["--name", "ioe", entry]


def main(argv: list[str]) -> int:
    targets = argv or ["gui", "cli"]
    for target in targets:
        command = _command(target)
        print(">", " ".join(command))
        code = subprocess.call(command, cwd=ROOT)
        if code:
            return code
    data_src = os.path.join(ROOT, "data")
    data_dst = os.path.join(ROOT, "dist", "data")
    if os.path.isdir(data_src):
        shutil.copytree(data_src, data_dst, dirs_exist_ok=True,     # .exe đọc data/ cạnh nó
                        ignore=shutil.ignore_patterns("browser_*", "vision", "fixtures", "*.log"))
    print("Xong: dist/ đã có sẵn data/ (config, session, answers).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
