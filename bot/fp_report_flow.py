"""Ежемесячный отчёт для финпартнёров: сбор данных и диалог с управляющим.

Поток: /fp_report → проект (только с включённым отчётом ФП) → три файла в чат: шаблон на этот месяц
(.docx, пустой), итоги инвентаризации (.xlsx) и отчёт за прошлый месяц (.docx, заполненный; можно
написать «без прошлого отчёта») → данные из НИМБ → вопросы по одному в порядке шаблона →
готовый report_park_ГГГГММ.docx уходит тому, кто собирал, и владельцу.

Из отчёта за прошлый месяц бот берёт то, что нельзя вытащить из НИМБ: задачи, факты операционных
показателей, план выплат, ФИО управляющего. Месяц всегда предыдущий. Состояние сессии хранится
файлами (fp_report.session), поэтому перезапуск бота не сбрасывает сбор.
"""

import asyncio
from datetime import date, timedelta
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import OWNER_TELEGRAM_IDS
from config.timeutil import now as tz_now
from fp_report.build import _MONTH_RU, ASK, build_report, count_open_fields
from fp_report.collect import fetch_nimba_inputs
from fp_report.inventory import load_save_positions
from fp_report.previous import expected_label, is_blank_template, month_label_of, read_previous
from fp_report.questions import apply_answer, build_questions, describe_field, missing_fields
from fp_report.session import delete_session, load_inputs, load_session, save_inputs, save_session, session_dir
from monitoring.managers import get_markets_for_manager, has_fp_report_access, is_owner
from monitoring.markets import get_market, list_markets

# uid, для которых сбор из НИМБ идёт прямо сейчас в этом процессе
_collecting: set[int] = set()

_TEMPLATE_NAME = "template.docx"
_INVENTORY_NAME = "inventory.xlsx"
_PREVIOUS_NAME = "previous.docx"
_INCOMING_NAME = "incoming.docx"
_NO_PREVIOUS_PHRASES = ("без прошлого отчёта", "без прошлого отчета", "без прошлого", "нет прошлого отчёта")
_SAME_MANAGER_PHRASES = ("тот же", "та же", "то же")


def _previous_month() -> str:
    first = tz_now().date().replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def _month_before(month: str) -> str:
    first = date(*map(int, month.split("-")), 1)
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


def _missing_files(uid: int, session: dict) -> list[str]:
    directory = session_dir(uid)
    missing = []
    if not (directory / _TEMPLATE_NAME).exists():
        missing.append("шаблон на этот месяц (.docx, пустой)")
    if not (directory / _INVENTORY_NAME).exists():
        missing.append("итоги инвентаризации (.xlsx)")
    if not (directory / _PREVIOUS_NAME).exists() and not session.get("no_previous"):
        missing.append("отчёт за прошлый месяц (.docx) или «без прошлого отчёта»")
    return missing


async def on_fp_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    message = update.effective_message
    session = load_session(uid)
    if session:
        await message.reply_text("Отчёт для ФП уже собирается. Доделай его или напиши «отмена», чтобы начать заново.")
        await _ask_waiting_step(message, uid, session)
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
        "Пришли три файла, порядок любой:\n"
        "1) шаблон отчёта ФП на этот месяц (.docx, пустой);\n"
        "2) итоги инвентаризации (.xlsx), возьму только строки со статусом SAVE;\n"
        "3) отчёт за прошлый месяц (.docx, заполненный). Если его нет, напиши «без прошлого отчёта»."
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
    directory = session_dir(uid)
    directory.mkdir(parents=True, exist_ok=True)

    if file_name.endswith(".xlsx"):
        target, label = directory / _INVENTORY_NAME, "итоги инвентаризации"
    elif file_name.endswith(".docx"):
        incoming = directory / _INCOMING_NAME
        telegram_file = await document.get_file()
        await telegram_file.download_to_drive(str(incoming))
        if is_blank_template(str(incoming)):
            target, label = directory / _TEMPLATE_NAME, "шаблон на этот месяц"
        else:
            found = month_label_of(str(incoming))
            wanted = expected_label(_month_before(session["month"]))
            if found != wanted:
                incoming.unlink(missing_ok=True)
                await message.reply_text(f"Это отчёт за «{found}», а нужен за «{wanted}». Пришли нужный или пустой шаблон.")
                return True
            target, label = directory / _PREVIOUS_NAME, "отчёт за прошлый месяц"
            session["no_previous"] = False
        incoming.replace(target)
        save_session(uid, session)
        await _after_file(message, context, uid, session, label)
        return True
    else:
        await message.reply_text("Нужны файлы .docx (шаблон и прошлый отчёт) и .xlsx (инвентаризация). Другие файлы не нужны.")
        return True

    telegram_file = await document.get_file()
    await telegram_file.download_to_drive(str(target))
    await _after_file(message, context, uid, session, label)
    return True


async def _after_file(message, context: ContextTypes.DEFAULT_TYPE, uid: int, session: dict, label: str) -> None:
    missing = _missing_files(uid, session)
    if missing:
        await message.reply_text(f"Получил: {label}. Жду ещё: {'; '.join(missing)}.")
        return
    await _start_collect(message, context, uid, session)


async def _start_collect(message, context: ContextTypes.DEFAULT_TYPE, uid: int, session: dict) -> None:
    session["stage"] = "collecting"
    save_session(uid, session)
    await message.reply_text("Все файлы получены. Тяну данные из НИМБ, это займёт пару минут.")
    await _collect(message, context, uid, session)


def _collect_sync(uid: int, spot_key: str, month: str) -> tuple[dict, list[dict]]:
    """Синхронная часть: НИМБ + разбор инвентаризации и прошлого отчёта + вопросы. Через asyncio.to_thread."""
    inputs = fetch_nimba_inputs(spot_key, month)
    positions = load_save_positions(str(session_dir(uid) / _INVENTORY_NAME))
    previous_path = session_dir(uid) / _PREVIOUS_NAME
    previous = read_previous(str(previous_path)) if previous_path.exists() else None
    return inputs, build_questions(inputs, positions, month, previous)


async def _collect(message, context: ContextTypes.DEFAULT_TYPE, uid: int, session: dict) -> None:
    market = get_market(session["market_id"])
    _collecting.add(uid)
    try:
        inputs, questions = await asyncio.to_thread(_collect_sync, uid, market["surf_spot_key"], session["month"])
    except Exception as exc:
        session["stage"] = "retry"
        save_session(uid, session)
        await message.reply_text(
            f"Не получилось собрать данные из НИМБ: {exc}\nКогда проблема уйдёт, напиши «повтор». Или «отмена»."
        )
        return
    finally:
        _collecting.discard(uid)

    save_inputs(uid, inputs)
    session.update({"stage": "questions", "questions": questions, "index": 0, "answers": {}})
    save_session(uid, session)
    await message.reply_text(f"Данные из НИМБ получены. Вопросов по отчёту: {len(questions)}. Отвечаю по одному.")
    await _ask_current(message, session)


async def _ask_current(message, session: dict) -> None:
    question = session["questions"][session["index"]]
    total = len(session["questions"])
    await message.reply_text(f"Вопрос {session['index'] + 1} из {total}.\n\n{question['text']}")


async def _ask_waiting_step(message, uid: int, session: dict) -> None:
    stage = session["stage"]
    if stage == "files":
        missing = _missing_files(uid, session)
        await message.reply_text("Жду файлы: " + "; ".join(missing) + ".")
    elif stage == "collecting":
        if uid in _collecting:
            await message.reply_text("Данные из НИМБ ещё собираются, подожди.")
        else:
            await message.reply_text(
                "Сбор данных из НИМБ прервался, например при перезапуске бота. Напиши «повтор», и я соберу его заново. "
                "Файлы и ответы сохранились."
            )
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
    lowered = text.lower()

    if lowered == "отмена":
        delete_session(uid)
        await message.reply_text("Сбор отчёта для ФП отменён.")
        return True

    if session["stage"] in ("retry", "collecting") and lowered == "повтор" and uid not in _collecting:
        session["stage"] = "collecting"
        save_session(uid, session)
        await _collect(message, context, uid, session)
        return True

    if session["stage"] == "files" and lowered in _NO_PREVIOUS_PHRASES:
        session["no_previous"] = True
        save_session(uid, session)
        await message.reply_text("Понял, без прошлого отчёта. Задачи и показатели прошлого месяца спрошу у тебя.")
        missing = _missing_files(uid, session)
        if not missing:
            await _start_collect(message, context, uid, session)
        else:
            await message.reply_text("Жду файлы: " + "; ".join(missing) + ".")
        return True

    if session["stage"] != "questions":
        await _ask_waiting_step(message, uid, session)
        return True

    if not text:
        await message.reply_text("Ответь текстом. Если пусто, напиши «нет».")
        return True

    question = session["questions"][session["index"]]
    if question["key"] == "manager_name" and lowered in _SAME_MANAGER_PHRASES:
        previous_path = session_dir(uid) / _PREVIOUS_NAME
        if previous_path.exists():
            text = read_previous(str(previous_path))["manager_name"] or text

    session["answers"].update(apply_answer(question, text))
    still = missing_fields(question, session["answers"])
    if still:
        save_session(uid, session)
        await message.reply_text(
            "Не хватает ответа: " + "; ".join(describe_field(f) for f in still)
            + ".\nПришли все пункты, каждый с новой строки. Для цифр — только число."
        )
        return True

    session["index"] += 1
    if session["index"] < len(session["questions"]):
        save_session(uid, session)
        await _ask_current(message, session)
        return True

    # Все вопросы пройдены, но что-то могло остаться незакрытым — возвращаем к первому такому
    pending = next((i for i, q in enumerate(session["questions"]) if missing_fields(q, session["answers"])), None)
    if pending is not None:
        session["index"] = pending
        save_session(uid, session)
        await message.reply_text("Остались незакрытые вопросы, без них отчёт не соберу.")
        await _ask_current(message, session)
        return True

    await _finish(message, context, uid, session)
    return True


def _previous_defaults(uid: int) -> dict:
    """Значения из отчёта за прошлый месяц, которые подставляются в отчёт без вопроса."""
    previous_path = session_dir(uid) / _PREVIOUS_NAME
    if not previous_path.exists():
        return {}
    previous = read_previous(str(previous_path))
    return {f"prev_ops:{label}": value for label, value in previous["ops_fact"].items()}


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
        answers={**_previous_defaults(uid), **session["answers"]},
    )
    open_fields = count_open_fields(path)
    caption = f"Отчёт для ФП: {market['name']}, {_month_label(month)}."
    if open_fields:
        caption += f"\nНезаполненных мест: {open_fields} (помечены «{ASK}»)."

    recipients = {uid} | {int(owner) for owner in OWNER_TELEGRAM_IDS if owner}
    for chat_id in recipients:
        with open(path, "rb") as fh:
            await context.bot.send_document(chat_id=chat_id, document=fh, filename=Path(path).name, caption=caption)

    delete_session(uid)
