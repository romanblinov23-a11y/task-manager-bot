"""Запуск сборки отчёта для ФП из файлов выгрузок.

    python3 -m fp_report.run 2026-09 \
        --pnl <pnl.json> --manager <manager_report.json> \
        --vozn <vozn.json> --komanda <komanda.json> --shtat <shtat.json> \
        --grafik <grafik.json> --guests <guests.json> --guests-prev <guests_prev.json> \
        --inventory <inventory.xlsx> --template <shablonotcheta.docx> --out <папка>

Все JSON — ответы НИМБ как есть (с обёрткой success/data или без неё).
"""

import argparse
import json
from pathlib import Path

from fp_report.build import build_report
from fp_report.inventory import load_save_positions


def _data(path: str) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return raw["data"] if isinstance(raw, dict) and "data" in raw else raw


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("month")
    p.add_argument("--pnl", required=True)
    p.add_argument("--manager", required=True)
    p.add_argument("--vozn", required=True)
    p.add_argument("--komanda", required=True)
    p.add_argument("--shtat", required=True)
    p.add_argument("--grafik", required=True)
    p.add_argument("--guests", required=True)
    p.add_argument("--guests-prev", required=True)
    p.add_argument("--inventory", required=True)
    p.add_argument("--template", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()

    path = build_report(
        month=a.month,
        template_path=a.template,
        pnl=_data(a.pnl),
        manager=_data(a.manager),
        vozn=_data(a.vozn),
        komanda=_data(a.komanda),
        shtat=_data(a.shtat),
        grafik=_data(a.grafik),
        guests=_data(a.guests),
        guests_prev=_data(a.guests_prev),
        inventory_positions=load_save_positions(a.inventory),
        out_dir=a.out,
    )
    print("saved:", path)


if __name__ == "__main__":
    main()
