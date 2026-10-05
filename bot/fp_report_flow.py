"""Ежемесячный отчёт для финпартнёров: сбор данных и диалог с управляющим.

Поток: /fp_report → проект (только с включённым отчётом ФП) → два файла в чат (шаблон .docx
и инвентаризация .xlsx) → данные из НИМБ → вопросы по одному в порядке шаблона → готовый
report_park_ГГГГММ.docx уходит тому, кто собирал, и владельцу.

Месяц всегда предыдущий. Состояние сессии хранится файлами (fp_report.session), поэтому
перезапуск бота не сбрасывает сбор.
"""

import asyncio
from datetime import timedelta
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import OWNER_TELEGRAM_IDS
from config.timeutil import now as tz_now
from fp_report.build import _MONTH_RU, ASK, build_report, count_open_fields
from fp_report.collect import fetch_nimba_inputs
from fp_report.inventory import load_save_positions
from fp_report.questions import apply_answer, build_questions
from fp_report.session import delete_session, load_inputs, load_session, save_inputs, save_session, session_dir
from monitoring.managers import get_markets_for_manager, has_fp_report_access, is_owner
from monitoring.markets import get_market, list_markets

_TEMPLATE_NAME = "template.docx"
_INVENTORY_NAME = "inventory.xlsx"


def _previous_month() -> str:
    first = tz_now().date().replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def _month_label(month: str) -> str:
    y, m = map(int, month.split("-"))
    return f"{_MONTH_RU[m]} {y}"


def _available_markets(uid: int) -> list[dict]:
    """Владелец видит все рынки, управляющий — свои. Нужны включённый флаг и привязка к точке Surf."""
    if is_owner(uid):
        markets = list_markets()
    elif has_fp_report_access(uid):
        markets = get_markets_for_manager(uid)
    else:
        return []
    return [m for m in markets if m.get("fp_report_enabled") and m.get("surf_spot_key")]


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"fpr_market:{m['id']}")] for m in markets])


async def on_fp_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    message = update.effective_message
    session = load_session(uid)
    if session:
        await message.reply_text("Отчёт для ФП уже собирается. Доделай его или напиши «отмена», чтобы начать заново.")
        await _ask_waiting_step(message, session)
        return
    markets = _available_markets(uid)
    if not markets:
        await message.reply_text(
            "Ежемесячный отчёт для ФП пока не включён ни по одному проекту. Владелец включает его в "
            "/market_settings — у проекта должна быть привязка к точке Surf Coffee."
        )
        return
    if len(markets) == 1:
        await _start(message, uid, markets[0])
        return
    await message.reply_text("По какому проекту собираем отчёт для ФП?", reply_markup=_market_pick_keyboard(markets))


async def on_fp_report_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if load_session(uid):
        await query.edit_message_text("Отчёт для ФП уже собирается. Доделай его или напиши «отмена».")
        return
    market_id = int(query.data.split(":", 1)[1])
    market = next((m for m in _available_markets(uid) if m["id"] == market_id), None)
    if not market:
        await query.edit_message_text("Этот проект недоступен для отчёта ФП.")
        return
    await query.edit_message_text(f"Проект: {market['name']}")
    await _start(query.message, uid, market)


async def _start(message, uid: int, market: dict) -> None:
    month = _previous_month()
    save_session(uid, {"market_id": market["id"], "month": month, "stage": "files", "questions": [], "index": 0, "answers": {}})
    await message.reply_text(
        f"Собираем отчёт для ФП за {_month_label(month)} по «{market['name']}».\n"
        "Пришли два файла: шаблон отчёта ФП (.docx) и итоги инвентаризации (.xlsx). "
        "Из инвентаризации возьму только строки со статусом SAVE. Порядок любой."
    )


async def on_fp_report_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Файл от управляющего/владельца в ходе сбора. False — если сбора сейчас нет, файл пойдёт дальше по старой логике."""
    uid = update.effective_user.id
    session = load_session(uid)
    if not session or session["stage"] != "files":
        return False
    message = update.effective_message
    document = message.document
    file_name = (document.file_name or "").lower()
    if file_name.endswith(".docx"):
        target, label, missing = _TEMPLATE_NAME, "шаблон отчёта", "итоги инвентаризации (.xlsx)"
    elif file_name.endswith(".xlsx"):
        target, label, missing = _INVENTORY_NAME, "итоги инвентаризации", "шаблон отчёта (.docx)"
    else:
        await message.reply_text("Для отчёта ФП нужны два файла: шаблон (.docx) и инвентаризация (.xlsx). Другие файлы не нужны.")
        return True

    directory = session_dir(uid)
    directory.mkdir(parents=True, exist_ok=True)
    telegram_file = await document.get_file()
    await telegram_file.download_to_drive(str(directory / target))

    if not (directory / _TEMPLATE_NAME).exists() or not (directory / _INVENTORY_NAME).exists():
        await message.reply_text(f"Получил: {label}. Жду ещё: {missing}.")
        return True

    session["stage"] = "collecting"
    save_session(uid, session)
    await message.reply_text("Оба файла получены. Тяну данные из НИМБ, это займёт пару минут.")
    await _collect(message, context, uid, session)
    return True


def _collect_sync(uid: int, spot_key: str, month: str) -> tuple[dict, list[dict]]:
    """Синхронная часть: НИМБ + разбор инвентаризации + вопросы. Вызывается через asyncio.to_thread."""
    inputs = fetch_nimba_inputs(spot_key, month)
    positions = load_save_positions(str(session_dir(uid) / _INVENTORY_NAME))
    return inputs, build_questions(inputs, positions, month)


async def _collect(message, context: ContextTypes.DEFAULT_TYPE, uid: int, session: dict) -> None:
    market = get_market(session["market_id"])
    try:
        inputs, questions = await asyncio.to_thread(_collect_sync, uid, market["surf_spot_key"], session["month"])
    except Exception as exc:
        session["stage"] = "retry"
        save_session(uid, session)
        await message.reply_text(
            f"Не получилось собрать данные из НИМБ: {exc}\nКогда проблема уйдёт, напиши «повтор». Или «отмена»."
        )
        return

    save_inputs(uid, inputs)
    session.update({"stage": "questions", "questions": questions, "index": 0, "answers": {}})
    save_session(uid, session)
    await message.reply_text(f"Данные из НИМБ получены. Вопросов по отчёту: {len(questions)}. Отвечаю по одному.")
    await _ask_current(message, session)


async def _ask_current(message, session: dict) -> None:
    question = session["questions"][session["index"]]
    total = len(session["questions"])
    await message.reply_text(f"Вопрос {session['index'] + 1} из {total}.\n\n{question['text']}")


async def _ask_waiting_step(message, session: dict) -> None:
    stage = session["stage"]
    if stage == "files":
        await message.reply_text("Жду два файла: шаблон отчёта (.docx) и инвентаризацию (.xlsx).")
    elif stage == "collecting":
        await message.reply_text("Данные из НИМБ ещё собираются, подожди.")
    elif stage == "retry":
        await message.reply_text("Не вышло собрать данные из НИМБ. Напиши «повтор» или «отмена».")
    elif stage == "questions":
        await _ask_current(message, session)


async def on_fp_report_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Текстовый ответ в ходе сбора отчёта ФП. False — сбора нет, сообщение идёт дальше."""
    uid = update.effective_user.id
    session = load_session(uid)
    if not session:
        return False
    message = update.effective_message
    text = (message.text or "").strip()

    if text.lower() == "отмена":
        delete_session(uid)
        await message.reply_text("Сбор отчёта для ФП отменён.")
        return True

    if session["stage"] == "retry" and text.lower() == "повтор":
        session["stage"] = "collecting"
        save_session(uid, session)
        await _collect(message, context, uid, session)
        return True

    if session["stage"] != "questions":
        await _ask_waiting_step(message, session)
        return True

    if not text:
        await message.reply_text("Ответь текстом. Если пусто, напиши «нет».")
        return True

    question = session["questions"][session["index"]]
    session["answers"].update(apply_answer(question, text))
    session["index"] += 1
    if session["index"] < len(session["questions"]):
        save_session(uid, session)
        await _ask_current(message, session)
        return True

    await _finish(message, context, uid, session)
    return True


async def _finish(message, context: ContextTypes.DEFAULT_TYPE, uid: int, session: dict) -> None:
    market = get_market(session["market_id"])
    month = session["month"]
    directory = session_dir(uid)
    inputs = load_inputs(uid)
    positions = load_save_positions(str(directory / _INVENTORY_NAME))

    path = await asyncio.to_thread(
        build_report,
        month=month,
        template_path=str(directory / _TEMPLATE_NAME),
        pnl=inputs["pnl"],
        manager=inputs["manager"],
        vozn=inputs["vozn"],
        komanda=inputs["komanda"],
        shtat=inputs["shtat"],
        grafik=inputs["grafik"],
        inventory_positions=positions,
        out_dir=str(directory / "out"),
        spot_code=market["surf_spot_key"].split("_")[0],
        answers=session["answers"],
    )
    open_fields = count_open_fields(path)
    caption = f"Отчёт для ФП: {market['name']}, {_month_label(month)}."
    if open_fields:
        caption += f"\nОсталось пустых полей: {open_fields} (помечены «{ASK}»)."

    recipients = {uid} | {int(owner) for owner in OWNER_TELEGRAM_IDS if owner}
    for chat_id in recipients:
        with open(path, "rb") as fh:
            await context.bot.send_document(chat_id=chat_id, document=fh, filename=Path(path).name, caption=caption)

    delete_session(uid)
