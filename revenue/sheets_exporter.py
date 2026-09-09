"""
Экспорт P&L из НИБМ в Google Sheets.

Для каждой точки — своя таблица. Первый лист очищается и заполняется заново:
строки = показатели P&L (с отступом по уровню вложенности),
столбцы = план/факт по каждому из 12 месяцев + годовые итоги.

Авторизация через сервисный аккаунт Google — JSON-ключ берётся из
переменной окружения GOOGLE_SHEETS_CREDENTIALS.
"""

import json
import logging

import gspread

from config.settings import GOOGLE_SHEETS_CREDENTIALS
from revenue.surfcoffee_client import SurfCoffeeClient, SPOTS

logger = logging.getLogger("revenue.sheets_exporter")

SHEET_IDS = {
    "park_gorkogo": "1tBleuXsunKT7vDrQM0EYZaks1TxGBZLPWbrQ7YrnjVI",
    "yandex":       "1cosc545M-Bqa7ZLXA8b7lfxnbv36esGusTg4m2hs2As",
    "okko":         "1QuzGQeNzfjIjIYZ0-zOGcqtYODA1rsaTdov_aQQX_Kw",
}

MONTHS_RU = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
             "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]

# Показатель + 12*(план+факт) + год план + год факт
N_COLS = 1 + 12 * 2 + 2  # 27

# ─── Цветовая палитра ───────────────────────────────────────────────────────
def _rgb(r: float, g: float, b: float) -> dict:
    return {"red": r, "green": g, "blue": b}

# Все фоны светлые — везде чёрный шрифт
_BG_HEADER = _rgb(0.616, 0.765, 0.902)   # #9DC3E6  заголовок — средне-голубой
_BG_D0     = _rgb(0.741, 0.843, 0.929)   # #BDCFED  разделы 0-го уровня
_BG_D1_P   = _rgb(0.831, 0.898, 0.945)   # #D4E5F1  разделы 1-го уровня
_BG_D2_P   = _rgb(0.898, 0.937, 0.965)   # #E5EFF7  разделы 2-го уровня
_BG_LEAF1  = _rgb(0.945, 0.965, 0.980)   # #F1F6FA  листья 1-го уровня
_BG_LEAF2  = _rgb(0.973, 0.980, 0.988)   # #F8FAFC  листья 2-го уровня
_BG_WHITE  = _rgb(1.0,   1.0,   1.0)     # белый
_DARK      = _rgb(0.063, 0.063, 0.063)   # единственный цвет текста во всей таблице

_BORDER_DARK  = _rgb(0.530, 0.660, 0.800)  # рамка под цвет палитры
_BORDER_LIGHT = _rgb(0.800, 0.845, 0.886)  # тонкая сетка


def _row_style(depth: int, has_children: bool) -> tuple[dict, dict, bool]:
    """Возвращает (фон, цвет_текста, жирный) для строки данных."""
    if depth == 0:
        return _BG_D0, _DARK, True
    if depth == 1 and has_children:
        return _BG_D1_P, _DARK, True
    if depth == 1:
        return _BG_LEAF1, _DARK, False
    if depth == 2 and has_children:
        return _BG_D2_P, _DARK, True
    if depth == 2:
        return _BG_LEAF2, _DARK, False
    return _BG_WHITE, _DARK, False


# ─── Построение плоской таблицы ─────────────────────────────────────────────

def _header_row(year: int) -> list:
    row = ["Показатель"]
    for m in MONTHS_RU:
        row += [f"{m} план", f"{m} факт"]
    row += ["Год план", "Год факт"]
    return row


def _flatten(fields: list, year: int, depth: int = 0) -> list[tuple[list, int, str, bool]]:
    """
    Рекурсивно разворачивает дерево P&L в плоский список.
    Каждый элемент: (row_data, depth, ftype, has_children).
    """
    rows = []
    prefix = "  " * depth
    for field in fields:
        title = field.get("title", "")
        ftype = field.get("type", "number")
        has_children = bool(field.get("items"))
        results_by_month = {r["period"][:7]: r for r in (field.get("results") or [])}
        yearly = field.get("yearly") or {}

        row = [prefix + title]
        for m in range(1, 13):
            key = f"{year}-{m:02d}"
            r = results_by_month.get(key, {})
            plan = r.get("plan") or 0
            fact = r.get("fact") or 0
            if ftype == "percent":
                row += [f"{plan:.1f}%" if plan else "", f"{fact:.1f}%" if fact else ""]
            else:
                row += [round(plan, 2) if plan else 0, round(fact, 2) if fact else 0]

        if yearly:
            yp = yearly.get("plan") or 0
            yf = yearly.get("fact") or 0
            if ftype == "percent":
                row += [f"{yp:.1f}%" if yp else "", f"{yf:.1f}%" if yf else ""]
            else:
                row += [round(yp, 2) if yp else 0, round(yf, 2) if yf else 0]
        else:
            row += ["", ""]

        rows.append((row, depth, ftype, has_children))
        if has_children:
            rows.extend(_flatten(field["items"], year, depth + 1))

    return rows


# ─── Вычисление диапазонов для группировки строк ────────────────────────────

def _compute_row_groups(row_meta: list[tuple[int, str, bool]]) -> list[tuple[int, int]]:
    """
    Возвращает список (startIndex, endIndex) для addDimensionGroup.
    Индексы 0-based; endIndex не включается (как в Sheets API).
    Строка заголовка = sheet index 0; данные начинаются с sheet index 1.
    """
    groups = []
    n = len(row_meta)
    for i, (depth, _, has_children) in enumerate(row_meta):
        if not has_children:
            continue
        j = i + 1
        while j < n and row_meta[j][0] > depth:
            j += 1
        if j > i + 1:
            # дочерние строки: row_meta[i+1 .. j-1]
            # sheet 0-based: i+2 .. j  →  [i+2, j+1)
            groups.append((i + 2, j + 1))
    return groups


# ─── Форматирование через batchUpdate ────────────────────────────────────────

def _apply_formatting(ws, row_meta: list[tuple[int, str, bool]]) -> None:
    """Применяет цвета, шрифты, группировки и размеры через Sheets API batchUpdate."""
    sid = ws.id
    requests = []

    # Заморозка строки 1 и столбца A
    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sid,
                "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1},
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }
    })

    # Ширина столбца A
    requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 300},
            "fields": "pixelSize",
        }
    })

    # Ширина данных-колонок
    requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 1, "endIndex": N_COLS},
            "properties": {"pixelSize": 95},
            "fields": "pixelSize",
        }
    })

    # Заголовок
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": _BG_HEADER,
                    "textFormat": {
                        "bold": True,
                        "foregroundColor": _DARK,
                        "fontFamily": "Montserrat",
                        "fontSize": 9,
                    },
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP",
                }
            },
            "fields": "userEnteredFormat",
        }
    })

    # Строки данных
    for i, (depth, ftype, has_children) in enumerate(row_meta):
        row_idx = i + 1  # 0-based sheet row
        bg, fg, bold = _row_style(depth, has_children)

        # Колонка A: название показателя
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sid,
                    "startRowIndex": row_idx,
                    "endRowIndex": row_idx + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": bg,
                        "textFormat": {
                            "bold": bold,
                            "italic": not has_children,
                            "foregroundColor": fg,
                            "fontFamily": "Montserrat",
                            "fontSize": 9,
                        },
                        "verticalAlignment": "MIDDLE",
                    }
                },
                "fields": "userEnteredFormat",
            }
        })

        # Колонки B–AA: данные план/факт
        data_fmt: dict = {
            "backgroundColor": bg,
            "textFormat": {
                "bold": bold,
                "foregroundColor": fg,
                "fontFamily": "Montserrat",
                "fontSize": 9,
            },
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
        }
        if ftype == "number":
            data_fmt["numberFormat"] = {"type": "NUMBER", "pattern": "#,##0"}

        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sid,
                    "startRowIndex": row_idx,
                    "endRowIndex": row_idx + 1,
                    "startColumnIndex": 1,
                    "endColumnIndex": N_COLS,
                },
                "cell": {"userEnteredFormat": data_fmt},
                "fields": "userEnteredFormat",
            }
        })

    # ─── Границы ────────────────────────────────────────────────────────────
    n_rows = len(row_meta) + 1  # +1 заголовок

    def _border(style: str, color: dict) -> dict:
        return {"style": style, "color": color}

    # Вся таблица: тонкая сетка внутри, средняя рамка снаружи
    requests.append({
        "updateBorders": {
            "range": {
                "sheetId": sid,
                "startRowIndex": 0,
                "endRowIndex": n_rows,
                "startColumnIndex": 0,
                "endColumnIndex": N_COLS,
            },
            "top":             _border("SOLID", _BORDER_DARK),
            "bottom":          _border("SOLID", _BORDER_DARK),
            "left":            _border("SOLID", _BORDER_DARK),
            "right":           _border("SOLID", _BORDER_DARK),
            "innerHorizontal": _border("SOLID", _BORDER_LIGHT),
            "innerVertical":   _border("SOLID", _BORDER_LIGHT),
        }
    })

    # Толстая нижняя граница под заголовком
    requests.append({
        "updateBorders": {
            "range": {
                "sheetId": sid,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": N_COLS,
            },
            "bottom": _border("SOLID_MEDIUM", _BORDER_DARK),
        }
    })

    # Толстая верхняя граница перед каждым разделом depth-0 (разделитель секций)
    for i, (depth, _, _has_ch) in enumerate(row_meta):
        if depth == 0:
            requests.append({
                "updateBorders": {
                    "range": {
                        "sheetId": sid,
                        "startRowIndex": i + 1,
                        "endRowIndex": i + 2,
                        "startColumnIndex": 0,
                        "endColumnIndex": N_COLS,
                    },
                    "top": _border("SOLID_MEDIUM", _BORDER_DARK),
                }
            })

    # Средние вертикальные разделители между месяцами (колонки план каждого месяца + Год)
    # Индексы "план"-колонок: 1, 3, 5, ..., 23, 25
    for col in [*range(1, 25, 2), 25]:
        requests.append({
            "updateBorders": {
                "range": {
                    "sheetId": sid,
                    "startRowIndex": 0,
                    "endRowIndex": n_rows,
                    "startColumnIndex": col,
                    "endColumnIndex": col + 1,
                },
                "left": _border("SOLID_MEDIUM", _BORDER_DARK),
            }
        })

    # Группировки строк (кнопки сворачивания)
    for start, end in _compute_row_groups(row_meta):
        requests.append({
            "addDimensionGroup": {
                "range": {
                    "sheetId": sid,
                    "dimension": "ROWS",
                    "startIndex": start,
                    "endIndex": end,
                }
            }
        })

    ws.spreadsheet.batch_update({"requests": requests})


# ─── Публичные функции ────────────────────────────────────────────────────────

def _export_one_spot(gc: gspread.Client, surf_client: SurfCoffeeClient, spot_key: str, year: int) -> str:
    spot_title = SPOTS[spot_key]["title"]
    sheet_id = SHEET_IDS[spot_key]
    logger.info("Экспорт P&L %s (%d) → %s", spot_title, year, sheet_id)

    raw = surf_client.get_pnl_year(spot_key, year)
    fields = raw.get("fields", [])
    flat = _flatten(fields, year)

    rows = [item[0] for item in flat]
    row_meta = [(item[1], item[2], item[3]) for item in flat]

    ws = gc.open_by_key(sheet_id).sheet1
    ws.clear()
    ws.append_row(_header_row(year), value_input_option="USER_ENTERED")
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")

    try:
        _apply_formatting(ws, row_meta)
    except Exception:
        logger.exception("Форматирование не применилось для %s, данные записаны", spot_title)

    logger.info("Записано %d строк для %s", len(rows), spot_title)
    return f"📊 P&L {year} → {spot_title}: {len(rows)} строк выгружено в Google Sheets"


def _get_gc() -> gspread.Client:
    creds_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS)
    return gspread.service_account_from_dict(creds_dict)


def export_pnl_spot(surf_client: SurfCoffeeClient, spot_key: str, year: int) -> str:
    """Выгружает P&L одной точки. spot_key: 'aurora' | 'okko' | 'park_gorkogo'."""
    try:
        return _export_one_spot(_get_gc(), surf_client, spot_key, year)
    except Exception as e:
        logger.exception("Ошибка экспорта для %s", spot_key)
        return f"❌ Ошибка экспорта {SPOTS[spot_key]['title']}: {e}"


def export_pnl_to_sheets(surf_client: SurfCoffeeClient, year: int) -> str:
    """Выгружает P&L всех 3 точек. Возвращает сводный результат для Telegram."""
    gc = _get_gc()
    lines = []
    for spot_key, spot_info in SPOTS.items():
        try:
            _export_one_spot(gc, surf_client, spot_key, year)
            lines.append(f"✅ {spot_info['title']}")
        except Exception as e:
            logger.exception("Ошибка экспорта для %s", spot_key)
            lines.append(f"❌ {spot_info['title']}: {e}")

    return f"📊 P&L {year} → Google Sheets:\n" + "\n".join(lines)
