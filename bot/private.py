from bot.spot_events import on_events_reply
from bot.menu import on_menu_text
from bot.portfolio import on_portfolio_reply
from telegram import Update
from telegram.ext import ContextTypes

from bot.manager_report_flow import on_manager_report_reply
from bot.manager_report_lifecycle import reply as on_manager_report_lifecycle_reply
from bot.competitors import on_add_competitor_reply
from bot.fix_reading import on_fix_reading_reply
from bot.fp_report_flow import on_fp_report_document, on_fp_report_reply
from bot.handover_reports import on_handover_reply
from bot.handover_schedule_flow import on_set_handover_schedule_reply
from bot.handover_setup import on_set_handover_report_reply
from bot.manager_admin import on_manager_admin_reply
from bot.market_operator import on_set_consent_text_reply, on_set_operator_reply
from bot.meetings import on_meeting_agenda_reply, on_meeting_postpone_reply, on_meeting_schedule_reply
from bot.messaging import on_broadcast_document, on_broadcast_text_reply, on_message_chat_reply, on_message_reply
from monitoring.managers import is_owner
from bot.monitor_chats import on_register_monitor_chat_triggers_reply
from bot.monitoring_flow import on_monitoring_reply
from bot.monthly_plan_flow import on_set_monthly_plan_reply
from bot.onboarding import on_employee_message
from bot.report_chat_registration import on_register_report_chat_mention_reply
from bot.shift_report_setup import on_set_evening_report_reply
from bot.shift_reports import (
    on_shift_report_edit_reply,
    on_shift_report_more_info_reply,
    on_shift_report_reply,
    on_view_reports_edit_date_reply,
)
from bot.shift_schedule_flow import on_set_shift_schedule_reply
from bot.writeoff_plan_flow import on_set_writeoff_plan_reply
from config.settings import ROMAN_TELEGRAM_ID


async def on_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ответы на активные диалоги отчётов, настроек и регистрации."""
    if await on_menu_text(update, context):
        return

    if await on_broadcast_text_reply(update, context):
        return

    if await on_portfolio_reply(update, context):
        return

    if await on_manager_report_lifecycle_reply(update, context):
        return

    if await on_events_reply(update, context):
        return

    if await on_manager_admin_reply(update, context):
        return

    if await on_manager_report_reply(update, context):
        return

    if await on_fix_reading_reply(update, context):
        return

    if await on_add_competitor_reply(update, context):
        return

    if await on_monitoring_reply(update, context):
        return

    if await on_set_shift_schedule_reply(update, context):
        return

    if await on_set_evening_report_reply(update, context):
        return

    if await on_set_monthly_plan_reply(update, context):
        return

    if await on_set_writeoff_plan_reply(update, context):
        return

    if await on_shift_report_reply(update, context):
        return

    if await on_shift_report_edit_reply(update, context):
        return

    if await on_shift_report_more_info_reply(update, context):
        return

    if await on_view_reports_edit_date_reply(update, context):
        return

    if await on_set_handover_schedule_reply(update, context):
        return

    if await on_set_handover_report_reply(update, context):
        return

    if await on_handover_reply(update, context):
        return

    if await on_register_report_chat_mention_reply(update, context):
        return

    if await on_register_monitor_chat_triggers_reply(update, context):
        return

    if await on_message_reply(update, context):
        return

    if await on_message_chat_reply(update, context):
        return

    if await on_set_operator_reply(update, context):
        return

    if await on_set_consent_text_reply(update, context):
        return

    if await on_meeting_schedule_reply(update, context):
        return

    if await on_meeting_agenda_reply(update, context):
        return

    if await on_meeting_postpone_reply(update, context):
        return

    if await on_fp_report_reply(update, context):
        return

    if str(update.effective_user.id) != str(ROMAN_TELEGRAM_ID):
        await on_employee_message(update, context)
    else:
        await message_help(update)


async def message_help(update: Update) -> None:
    await update.effective_message.reply_text("Открой /menu — нужные разделы доступны кнопками.")


async def on_private_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Файлы активной рассылки или отчета для учредителей."""
    if await on_broadcast_document(update, context):
        return
    if is_owner(update.effective_user.id):
        if await on_fp_report_document(update, context):return
        await update.effective_message.reply_text("Для рассылки открой /broadcast, выбери получателей и пришли текст и/или PDF. Перед отправкой будет предпросмотр. Этот файл никому не отправлен.")
        return
    if not await on_fp_report_document(update, context):
        await update.effective_message.reply_text("Для отчета управляющего открой /manager_report или выбери его в /menu. Бот задаст вопросы; документы загружать не нужно.")
