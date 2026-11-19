"""Chạy nhanh: python run.py run  (tương đương `python -m ioeauto run`)."""

import sys

from ioeauto.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
