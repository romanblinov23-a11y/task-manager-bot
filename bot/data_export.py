import io
import json
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.managers import get_manager, is_owner
from monitoring.markets import get_market, list_markets
from personal_data.consent import delete_consents_for_market, list_consents_for_market
from personal_data.profile import delete_profiles_for_market, list_profiles_for_market


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"expdata_market:{m['id']}")] for m in markets])


def _delete_prompt_keyboard(market_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🗑 Удалить мою копию", callback_data=f"expdata_delete:{market_id}")]]
    )


def _delete_confirm_keyboard(market_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Да, удалить", callback_data=f"expdata_delete_confirm:{market_id}"),
                InlineKeyboardButton("Отмена", callback_data="expdata_delete_cancel"),
            ]
        ]
    )


def _build_export(market: dict) -> dict:
    consents = list_consents_for_market(market["id"])
    profiles = {p["telegram_user_id"]: p for p in list_profiles_for_market(market["id"])}
    people = {}
    for c in consents:
        people.setdefault(c["telegram_user_id"], {})["consent"] = c
    for uid, profile in profiles.items():
        people.setdefault(uid, {})["profile"] = profile

    records = []
    for uid, data in people.items():
        manager = get_manager(uid)
        records.append(
            {
                "telegram_user_id": uid,
                "name": manager["name"] if manager else None,
                "position": manager["position"] if manager else None,
                "consent": data.get("consent"),
                "profile": data.get("profile"),
            }
        )

    return {
        "market": market["name"],
        "market_id": market["id"],
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "operator_name": market.get("operator_name") or None,
        "records": records,
    }


async def on_export_operator_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/export_operator_data — владелец выгружает персональные данные
    сотрудников одного рынка (согласия + анкетные данные) файлом — для
    передачи заказчику по окончании договора на управление точкой.
    Отдельно, вторым явным шагом — можно удалить свою копию."""
    if not is_owner(update.effective_user.id):
        return
    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка.")
        return
    if len(markets) == 1:
        await _send_export(update.effective_message, context, markets[0])
        return
    await update.effective_message.reply_text("По какому рынку выгружаем персональные данные?", reply_markup=_market_pick_keyboard(markets))


async def on_export_operator_data_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _send_export(query.message, context, market)


async def _send_export(message, context: ContextTypes.DEFAULT_TYPE, market: dict) -> None:
    export = _build_export(market)
    if not export["records"]:
        await message.reply_text(f"На «{market['name']}» пока нет ни одной записи персональных данных для выгрузки.")
        return

    payload = json.dumps(export, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"personal_data_{market['name']}_{datetime.now().date().isoformat()}.json"
    await context.bot.send_document(
        chat_id=message.chat.id, document=io.BytesIO(payload), filename=filename,
        caption=f"📦 Персональные данные «{market['name']}»: {len(export['records'])} чел. Готово для передачи заказчику.",
    )
    await message.reply_text(
        "Когда передача заказчику подтверждена — можно удалить свою копию этих данных.",
        reply_markup=_delete_prompt_keyboard(market["id"]),
    )


async def on_export_operator_data_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        f"⚠️ Точно удалить свою копию персональных данных «{market['name']}»? Действие необратимо — "
        "убедитесь, что заказчик уже получил выгрузку.",
        reply_markup=_delete_confirm_keyboard(market_id),
    )


async def on_export_operator_data_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    await query.answer("Удаляю…")
    deleted_consents = delete_consents_for_market(market_id)
    deleted_profiles = delete_profiles_for_market(market_id)
    market_name = market["name"] if market else f"#{market_id}"
    await query.edit_message_text(
        f"✅ Удалено для «{market_name}»: согласий — {deleted_consents}, анкет — {deleted_profiles}."
    )


async def on_export_operator_data_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Отменено, данные не удалены.")
