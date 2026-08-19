from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.timeutil import parse_date
from monitoring.competitors import list_competitors
from monitoring.managers import is_owner
from monitoring.markets import get_market, list_markets
from monitoring.readings import import_historical_reading

# telegram_user_id (str) владельца -> {"market_id": int} — ждём вставки текста со снятиями
_awaiting_paste: dict[str, dict] = {}

# telegram_user_id (str) владельца -> {"market_id": int, "rows": [...]} — распарсено, ждём подтверждения
_pending_confirm: dict[str, dict] = {}


def _market_pick_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(m["name"], callback_data=f"impr_market:{m['id']}")] for m in list_markets()]
    )


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Подтвердить", callback_data="impr_confirm"), InlineKeyboardButton("❌ Отмена", callback_data="impr_cancel")]]
    )


async def on_import_readings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/import_readings — массовый импорт накопленных исторических снятий
    (показатель конкурента за прошлые недели), минуя обычный еженедельный
    цикл /monitoring. Только для владельца."""
    if not is_owner(update.effective_user.id):
        return
    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка — сначала /add_project.")
        return
    await update.effective_message.reply_text("По какому рынку импортируем исторические данные?", reply_markup=_market_pick_keyboard())


async def on_import_readings_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return

    competitors = list_competitors(market_id, include_closed=True)
    if not competitors:
        await query.answer()
        await query.edit_message_text(
            f"На рынке «{market['name']}» пока нет ни одного конкурента — сначала добавьте их через /add_competitor."
        )
        return

    codes_list = "\n".join(f"— {c['code']} ({c['name']})" for c in competitors)
    owner_id = str(query.from_user.id)
    _awaiting_paste[owner_id] = {"market_id": market_id}
    await query.answer()
    await query.edit_message_text(
        f"Рынок «{market['name']}». Известные точки:\n{codes_list}\n\n"
        "Вставьте данные текстом, по одному снятию на строку:\n"
        "код или название · дата · показатель\n\n"
        "Название можно писать как в списке выше (можно с пробелами, например «Surf Coffee»). "
        "Дата — в любом привычном формате (01.07.2026, 2026-07-01, «9 июля»), последнее число в строке — показатель. "
        "Разделитель — пробел или таб, можно вставлять прямо из таблицы."
    )


def _parse_paste(text: str, competitors: list[dict]) -> tuple[list[dict], list[dict]]:
    by_key: dict[str, dict] = {}
    for c in competitors:
        by_key[c["code"].strip().lower()] = c
        by_key[c["name"].strip().lower()] = c

    rows, errors = [], []
    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        tokens = line.split()
        if len(tokens) < 3:
            errors.append({"line_no": i, "raw": line, "reason": "меньше трёх полей (точка, дата, показатель)"})
            continue

        # Название может быть из нескольких слов ("Surf Coffee") — дата и
        # показатель всегда два последних токена в строке, всё до них — код
        # или название точки.
        value_raw, date_raw = tokens[-1], tokens[-2]
        name_or_code = " ".join(tokens[:-2])
        competitor = by_key.get(name_or_code.strip().lower())
        if not competitor:
            errors.append({"line_no": i, "raw": line, "reason": f"неизвестная точка «{name_or_code}»"})
            continue

        reading_at = parse_date(date_raw)
        if not reading_at:
            errors.append({"line_no": i, "raw": line, "reason": f"не смог понять дату «{date_raw}»"})
            continue

        try:
            value = float(value_raw.replace(",", "."))
        except ValueError:
            errors.append({"line_no": i, "raw": line, "reason": f"не смог понять показатель «{value_raw}»"})
            continue

        rows.append(
            {
                "competitor_id": competitor["id"],
                "code": competitor["code"],
                "name": competitor["name"],
                "reading_at": reading_at,
                "value": value,
            }
        )
    return rows, errors


async def on_import_readings_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает вставленный текст со снятиями для /import_readings.
    Возвращает True, если сообщение обработано — по конвенции остальных
    claim-хендлеров в on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_paste.get(owner_id)
    if not state:
        return False

    text = update.effective_message.text or ""
    market_id = state["market_id"]
    del _awaiting_paste[owner_id]

    competitors = list_competitors(market_id, include_closed=True)
    rows, errors = _parse_paste(text, competitors)

    lines = []
    if rows:
        lines.append(f"Распознано снятий: {len(rows)}")
        for r in sorted(rows, key=lambda x: (x["code"], x["reading_at"])):
            lines.append(f"— {r['code']} ({r['name']}): {r['reading_at']} — {r['value']:g}")
    if errors:
        lines.append("")
        lines.append(f"Не разобрал строк: {len(errors)}")
        for e in errors:
            lines.append(f"— строка {e['line_no']} «{e['raw']}»: {e['reason']}")

    if not rows:
        lines.append("")
        lines.append("Нечего импортировать. Проверьте формат и вставьте текст ещё раз.")
        _awaiting_paste[owner_id] = state
        await update.effective_message.reply_text("\n".join(lines))
        return True

    _pending_confirm[owner_id] = {"market_id": market_id, "rows": rows}
    lines.append("")
    lines.append(f"Подтвердить импорт {len(rows)} снятий?")
    await update.effective_message.reply_text("\n".join(lines), reply_markup=_confirm_keyboard())
    return True


async def on_import_readings_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    owner_id = str(query.from_user.id)
    state = _pending_confirm.pop(owner_id, None)
    if not state:
        await query.answer("Сессия неактуальна", show_alert=True)
        return

    await query.answer("Импортирую…")
    for row in state["rows"]:
        import_historical_reading(
            row["competitor_id"],
            row["value"],
            created_by=query.from_user.id,
            reading_at=row["reading_at"],
            note="импорт исторических данных",
        )
    await query.edit_message_text(f"✅ Импортировано снятий: {len(state['rows'])}.")


async def on_import_readings_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    _pending_confirm.pop(str(query.from_user.id), None)
    await query.answer()
    await query.edit_message_text("Отменено, ничего не импортировано.")
