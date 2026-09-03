import calendar
import logging
from datetime import time as dt_time

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot.chat_registration import on_register_project, on_register_project_choice
from bot.competitors import (
    on_add_competitor_command,
    on_add_competitor_factor_field_choice,
    on_add_competitor_factors_choice,
    on_add_competitor_format_choice,
    on_add_competitor_market_choice,
    on_add_competitor_own_first_choice,
    on_add_competitor_own_name_confirm,
    on_add_competitor_reading_choice,
    on_close_competitor_command,
    on_close_competitor_confirm,
    on_close_competitor_pick,
    on_close_market_choice,
    on_set_own_point_cancel,
    on_set_own_point_command,
    on_set_own_point_confirm,
    on_set_own_point_market_choice,
    on_set_own_point_pick,
)
from bot.confirmation import (
    on_confirmation_callback,
    on_edit_field_selected,
    on_employee_disambiguation,
    on_set_category,
)
from bot.daily_report import send_daily_report
from bot.dashboard_cmd import on_dashboard_aggregate_choice, on_dashboard_command, on_dashboard_market_choice
from bot.dashboard_tasks_cmd import on_dashboard_tasks_command
from bot.fix_reading import on_fix_reading_command, on_fix_reading_market_choice, on_fix_reading_pick
from bot.handlers import on_group_message
from bot.import_readings import (
    on_import_readings_cancel,
    on_import_readings_command,
    on_import_readings_confirm,
    on_import_readings_market_choice,
)
from bot.manager_admin import (
    on_add_project_command,
    on_manager_approve,
    on_manager_back_to_list,
    on_manager_blocks,
    on_manager_blocks_done,
    on_manager_chat_market,
    on_manager_chat_select,
    on_manager_chat_set_market,
    on_manager_chat_unbind,
    on_manager_chat_unbind_confirm,
    on_manager_chats,
    on_manager_legacy_remove,
    on_manager_legacy_remove_confirm,
    on_manager_market,
    on_manager_nudge,
    on_manager_onboarded,
    on_manager_reject,
    on_manager_remove,
    on_manager_remove_confirm,
    on_manager_role,
    on_manager_select,
    on_manager_set_market,
    on_manager_set_role,
    on_manager_toggle_block,
    on_managers_command,
    on_regulation_ack,
    on_reset_monitoring_cancel,
    on_reset_monitoring_command,
    on_reset_monitoring_confirm,
    on_reset_monitoring_market_choice,
    sync_employee_commands,
)
from bot.market_schedule import (
    on_schedule_command,
    on_schedule_day_toggle,
    on_schedule_done,
    on_schedule_market_choice,
)
from bot.monitoring_flow import (
    on_monitoring_assign_choice,
    on_monitoring_category_choice,
    on_monitoring_command,
    on_monitoring_date_choice,
    on_monitoring_factor_confirm,
    on_monitoring_factors_choice,
    on_monitoring_finish,
    on_monitoring_market_choice,
    on_monitoring_obs_choice,
    on_monitoring_pick,
    on_monitoring_ready,
    on_monitoring_skip,
    on_monitoring_start_button,
    send_monitoring_reminders,
)
from bot.onboarding import on_force_onboard, on_help, on_project_choice, on_role_choice, on_start
from bot.private import on_private_document, on_private_text, on_project_selected
from bot.regulations import on_regulations_command
from bot.mytasks_manage import on_mytasks_callback, on_mytasks_command
from bot.queries import (
    on_needhelp_command,
    on_stuck_command,
)
from bot.report_chat_registration import (
    on_register_report_chat,
    on_register_report_chat_market,
    on_register_report_chat_role,
    on_report_chats_command,
    on_report_chats_list,
    on_report_chats_unbind,
    on_report_chats_view,
)
from bot.shift_reports import (
    on_reset_shift_report_cancel,
    on_reset_shift_report_command,
    on_reset_shift_report_confirm,
    on_reset_shift_report_market_choice,
    on_send_shift_report_command,
    on_send_shift_report_market_choice,
    on_shift_report_absent,
    on_shift_report_command,
    on_shift_report_edit,
    on_shift_report_edit_cancel,
    on_shift_report_edit_field,
    on_shift_report_edit_reply,
    on_shift_report_fill,
    on_shift_report_fix_field,
    on_shift_report_manual_market_choice,
    on_shift_report_more_info,
    on_shift_report_more_info_reply,
    on_shift_report_owner_approve,
    on_shift_report_reply,
    on_shift_report_supervisor_approve,
    send_pending_reports,
    send_shift_report_escalations,
    send_shift_report_kickoffs,
)
from bot.shift_schedule_flow import (
    on_set_shift_schedule_cancel,
    on_set_shift_schedule_command,
    on_set_shift_schedule_confirm,
    on_set_shift_schedule_market_choice,
    on_set_shift_schedule_reply,
    send_shift_schedule_requests,
)
from bot.status_cycle import on_status_button, run_status_check
from bot.task_manage import on_employee_command, on_status_command, on_task_manage_callback
from bot.weekly_report import on_weekly_command, send_weekly_report
from config.settings import (
    DAILY_REPORT_TIME,
    MONITORING_REMINDER_TIME,
    OWNER_TELEGRAM_IDS,
    SHIFT_REPORT_DISPATCH_TIME,
    SHIFT_REPORT_ESCALATE_TIME,
    SHIFT_REPORT_START_TIME,
    SHIFT_SCHEDULE_REMINDER_TIME,
    STATUS_CHECK_TIME,
    TELEGRAM_BOT_TOKEN,
    TZ,
    WEEKLY_REPORT_DAY,
    WEEKLY_REPORT_TIME,
)
from config.timeutil import today as tz_today
from monitoring.db import init_schema as init_monitoring_schema
from monitoring.managers import list_managers
from tasks.db import init_schema as init_tasks_schema
from tasks.retention import purge_closed_tasks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


async def _status_check_job(context) -> None:
    await run_status_check(context.bot)


async def _daily_report_job(context) -> None:
    await send_daily_report(context.bot)


async def _weekly_report_job(context) -> None:
    await send_weekly_report(context.bot)


async def _monitoring_reminder_job(context) -> None:
    await send_monitoring_reminders(context.bot)


async def _task_retention_job(context) -> None:
    purged = purge_closed_tasks()
    if purged:
        logging.getLogger(__name__).info("Retention: удалено %d задач, закрытых до этого месяца", purged)


def _is_shift_schedule_request_day() -> bool:
    """14-е число или последний день месяца — дни, когда бот сам просит у
    управляющего график смен на 2 недели вперёд."""
    today = tz_today()
    last_day_of_month = calendar.monthrange(today.year, today.month)[1]
    return today.day == 14 or today.day == last_day_of_month


async def _shift_schedule_reminder_job(context) -> None:
    if _is_shift_schedule_request_day():
        await send_shift_schedule_requests(context.bot)


async def _shift_report_kickoff_job(context) -> None:
    await send_shift_report_kickoffs(context.bot)


async def _shift_report_escalate_job(context) -> None:
    await send_shift_report_escalations(context.bot)


async def _shift_report_dispatch_job(context) -> None:
    await send_pending_reports(context.bot)


def _parse_time(value: str, tzinfo) -> dt_time:
    hour, minute = (int(part) for part in value.split(":"))
    return dt_time(hour=hour, minute=minute, tzinfo=tzinfo)


_ROMAN_COMMANDS = [
    BotCommand("status", "Открытые задачи по проекту"),
    BotCommand("employee", "Задачи сотрудника по всем проектам"),
    BotCommand("stuck", "Подвисшие задачи"),
    BotCommand("needhelp", "Задачи, где нужна помощь"),
    BotCommand("dashboard_tasks", "Полная аналитика по задачам (все проекты)"),
    BotCommand("weekly", "Еженедельная аналитика по запросу"),
    BotCommand("managers", "Сотрудники бота и привязки чатов"),
    BotCommand("add_project", "Добавить проект/точку Surf"),
    BotCommand("reset_monitoring", "⚠️ Обнулить конкурентов на выбранном рынке"),
    BotCommand("import_readings", "Импорт исторических снятий по рынку"),
    BotCommand("fix_reading", "Исправить дату снятия у точки"),
    BotCommand("add_competitor", "Добавить конкурента на рынок"),
    BotCommand("close_competitor", "Закрыть/открыть конкурента"),
    BotCommand("set_own_point", "Исправить, какая точка на рынке наша"),
    BotCommand("schedule", "Настроить дни мониторинга рынка"),
    BotCommand("monitoring", "Провести мониторинг конкурентов"),
    BotCommand("dashboard_market", "Дашборд по рынку"),
    BotCommand("set_shift_schedule", "Загрузить график смен на 2 недели"),
    BotCommand("register_report_chat", "Привязать чат к рассылке отчётов смены"),
    BotCommand("report_chats", "Посмотреть/отвязать чаты рассылки отчётов"),
    BotCommand("shift_report", "Внести отчёт по смене принудительно"),
    BotCommand("send_shift_report", "Отправить сегодняшний отчёт сейчас (проверка формата)"),
    BotCommand("reset_shift_report", "⚠️ Сбросить сегодняшний отчёт по смене"),
    BotCommand("regulations", "Регламенты работы с ботом"),
    BotCommand("help", "Список команд"),
]

_ONBOARDING_COMMANDS = [BotCommand("start", "Пройти онбординг")]


async def _set_bot_commands(app) -> None:
    # По умолчанию — только /start, пока не пройден онбординг и владелец не
    # подтвердил доступ (см. bot.manager_admin.sync_employee_commands —
    # персональное меню появляется только после подтверждения).
    await app.bot.set_my_commands(_ONBOARDING_COMMANDS, scope=BotCommandScopeDefault())
    # Для владельцев в личке — полный список
    for owner_id in OWNER_TELEGRAM_IDS:
        await app.bot.set_my_commands(
            _ROMAN_COMMANDS,
            scope=BotCommandScopeChat(chat_id=int(owner_id)),
        )
    # Уже подтверждённым сотрудникам восстанавливаем их персональное меню —
    # иначе после смены дефолтного списка на /start они бы его потеряли.
    for manager in list_managers():
        if manager["status"] == "active":
            await sync_employee_commands(app.bot, manager["telegram_user_id"])


def main() -> None:
    init_monitoring_schema()
    init_tasks_schema()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(_set_bot_commands).build()

    app.add_handler(CommandHandler("start", on_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("help", on_help, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("weekly", on_weekly_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("status", on_status_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("employee", on_employee_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("stuck", on_stuck_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("needhelp", on_needhelp_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("register_project", on_register_project, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("onboard", on_force_onboard, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("mytasks", on_mytasks_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("regulations", on_regulations_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("managers", on_managers_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("add_project", on_add_project_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("reset_monitoring", on_reset_monitoring_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("import_readings", on_import_readings_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("fix_reading", on_fix_reading_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("add_competitor", on_add_competitor_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("close_competitor", on_close_competitor_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("set_own_point", on_set_own_point_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("schedule", on_schedule_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("monitoring", on_monitoring_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("dashboard_market", on_dashboard_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("dashboard_tasks", on_dashboard_tasks_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("set_shift_schedule", on_set_shift_schedule_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("register_report_chat", on_register_report_chat, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("report_chats", on_report_chats_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("shift_report", on_shift_report_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("send_shift_report", on_send_shift_report_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("reset_shift_report", on_reset_shift_report_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, on_group_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_private_text))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL, on_private_document))
    app.add_handler(CallbackQueryHandler(on_register_project_choice, pattern=r"^regchat:"))
    app.add_handler(CallbackQueryHandler(on_confirmation_callback, pattern=r"^confirm:"))
    app.add_handler(CallbackQueryHandler(on_edit_field_selected, pattern=r"^edit_field:"))
    app.add_handler(CallbackQueryHandler(on_set_category, pattern=r"^set_cat:"))
    app.add_handler(CallbackQueryHandler(on_project_selected, pattern=r"^project:"))
    app.add_handler(CallbackQueryHandler(on_employee_disambiguation, pattern=r"^employee:"))
    app.add_handler(CallbackQueryHandler(on_status_button, pattern=r"^status:"))
    app.add_handler(CallbackQueryHandler(on_project_choice, pattern=r"^onb_project:"))
    app.add_handler(CallbackQueryHandler(on_role_choice, pattern=r"^onb_role:"))
    app.add_handler(CallbackQueryHandler(on_manager_select, pattern=r"^mgr_select:"))
    app.add_handler(CallbackQueryHandler(on_manager_nudge, pattern=r"^mgr_nudge:"))
    app.add_handler(CallbackQueryHandler(on_manager_legacy_remove_confirm, pattern=r"^mgr_legacy_remove_confirm:"))
    app.add_handler(CallbackQueryHandler(on_manager_legacy_remove, pattern=r"^mgr_legacy_remove:"))
    app.add_handler(CallbackQueryHandler(on_manager_approve, pattern=r"^mgr_approve:"))
    app.add_handler(CallbackQueryHandler(on_manager_reject, pattern=r"^mgr_reject:"))
    app.add_handler(CallbackQueryHandler(on_manager_set_role, pattern=r"^mgr_setrole:"))
    app.add_handler(CallbackQueryHandler(on_manager_role, pattern=r"^mgr_role:"))
    app.add_handler(CallbackQueryHandler(on_manager_set_market, pattern=r"^mgr_setmarket:"))
    app.add_handler(CallbackQueryHandler(on_manager_market, pattern=r"^mgr_market:"))
    app.add_handler(CallbackQueryHandler(on_manager_remove_confirm, pattern=r"^mgr_remove_confirm:"))
    app.add_handler(CallbackQueryHandler(on_manager_remove, pattern=r"^mgr_remove:"))
    app.add_handler(CallbackQueryHandler(on_manager_back_to_list, pattern=r"^mgr_list$"))
    app.add_handler(CallbackQueryHandler(on_manager_toggle_block, pattern=r"^mgr_toggleblock:"))
    app.add_handler(CallbackQueryHandler(on_manager_blocks_done, pattern=r"^mgr_blocksdone:"))
    app.add_handler(CallbackQueryHandler(on_manager_blocks, pattern=r"^mgr_blocks:"))
    app.add_handler(CallbackQueryHandler(on_manager_onboarded, pattern=r"^mgr_onboarded$"))
    app.add_handler(CallbackQueryHandler(on_manager_chats, pattern=r"^mgr_chats$"))
    app.add_handler(CallbackQueryHandler(on_manager_chat_unbind_confirm, pattern=r"^mgr_chat_unbind_confirm:"))
    app.add_handler(CallbackQueryHandler(on_manager_chat_unbind, pattern=r"^mgr_chat_unbind:"))
    app.add_handler(CallbackQueryHandler(on_manager_chat_set_market, pattern=r"^mgr_chat_setmarket:"))
    app.add_handler(CallbackQueryHandler(on_manager_chat_market, pattern=r"^mgr_chat_market:"))
    app.add_handler(CallbackQueryHandler(on_manager_chat_select, pattern=r"^mgr_chat_select:"))
    app.add_handler(CallbackQueryHandler(on_regulation_ack, pattern=r"^reg_ack:"))
    app.add_handler(CallbackQueryHandler(on_reset_monitoring_market_choice, pattern=r"^reset_monitoring_market:"))
    app.add_handler(CallbackQueryHandler(on_reset_monitoring_confirm, pattern=r"^reset_monitoring_confirm:"))
    app.add_handler(CallbackQueryHandler(on_reset_monitoring_cancel, pattern=r"^reset_monitoring_cancel$"))
    app.add_handler(CallbackQueryHandler(on_fix_reading_market_choice, pattern=r"^fixr_market:"))
    app.add_handler(CallbackQueryHandler(on_fix_reading_pick, pattern=r"^fixr_pick:"))
    app.add_handler(CallbackQueryHandler(on_import_readings_market_choice, pattern=r"^impr_market:"))
    app.add_handler(CallbackQueryHandler(on_import_readings_confirm, pattern=r"^impr_confirm$"))
    app.add_handler(CallbackQueryHandler(on_import_readings_cancel, pattern=r"^impr_cancel$"))
    app.add_handler(CallbackQueryHandler(on_add_competitor_market_choice, pattern=r"^addc_market:"))
    app.add_handler(CallbackQueryHandler(on_add_competitor_format_choice, pattern=r"^addc_format:"))
    app.add_handler(CallbackQueryHandler(on_add_competitor_reading_choice, pattern=r"^addc_reading:"))
    app.add_handler(CallbackQueryHandler(on_add_competitor_factors_choice, pattern=r"^addc_factors:"))
    app.add_handler(CallbackQueryHandler(on_add_competitor_factor_field_choice, pattern=r"^addc_factor:"))
    app.add_handler(CallbackQueryHandler(on_add_competitor_own_first_choice, pattern=r"^addc_ownfirst:"))
    app.add_handler(CallbackQueryHandler(on_add_competitor_own_name_confirm, pattern=r"^addc_ownnameconfirm:"))
    app.add_handler(CallbackQueryHandler(on_close_market_choice, pattern=r"^cc_market:"))
    app.add_handler(CallbackQueryHandler(on_close_competitor_pick, pattern=r"^cc_pick:"))
    app.add_handler(CallbackQueryHandler(on_close_competitor_confirm, pattern=r"^cc_confirm:"))
    app.add_handler(CallbackQueryHandler(on_set_own_point_market_choice, pattern=r"^ownpt_market:"))
    app.add_handler(CallbackQueryHandler(on_set_own_point_pick, pattern=r"^ownpt_pick:"))
    app.add_handler(CallbackQueryHandler(on_set_own_point_confirm, pattern=r"^ownpt_confirm:"))
    app.add_handler(CallbackQueryHandler(on_set_own_point_cancel, pattern=r"^ownpt_cancel$"))
    app.add_handler(CallbackQueryHandler(on_schedule_market_choice, pattern=r"^sched_market:"))
    app.add_handler(CallbackQueryHandler(on_schedule_day_toggle, pattern=r"^sched_day:"))
    app.add_handler(CallbackQueryHandler(on_schedule_done, pattern=r"^sched_done:"))
    app.add_handler(CallbackQueryHandler(on_monitoring_market_choice, pattern=r"^monf_market:"))
    app.add_handler(CallbackQueryHandler(on_monitoring_start_button, pattern=r"^monf_go:"))
    app.add_handler(CallbackQueryHandler(on_monitoring_ready, pattern=r"^monf_ready$"))
    app.add_handler(CallbackQueryHandler(on_monitoring_pick, pattern=r"^monf_pick:"))
    app.add_handler(CallbackQueryHandler(on_monitoring_finish, pattern=r"^monf_finish$"))
    app.add_handler(CallbackQueryHandler(on_monitoring_assign_choice, pattern=r"^monf_assign:"))
    app.add_handler(CallbackQueryHandler(on_monitoring_skip, pattern=r"^monf_skip:"))
    app.add_handler(CallbackQueryHandler(on_monitoring_date_choice, pattern=r"^monf_date:"))
    app.add_handler(CallbackQueryHandler(on_monitoring_obs_choice, pattern=r"^monf_obs:"))
    app.add_handler(CallbackQueryHandler(on_monitoring_category_choice, pattern=r"^monf_cat:"))
    app.add_handler(CallbackQueryHandler(on_monitoring_factors_choice, pattern=r"^monf_factors:"))
    app.add_handler(CallbackQueryHandler(on_monitoring_factor_confirm, pattern=r"^monf_factorconfirm:"))
    app.add_handler(CallbackQueryHandler(on_dashboard_market_choice, pattern=r"^dash_market:"))
    app.add_handler(CallbackQueryHandler(on_dashboard_aggregate_choice, pattern=r"^dash_all$"))
    app.add_handler(CallbackQueryHandler(on_task_manage_callback, pattern=r"^tmg:"))
    app.add_handler(CallbackQueryHandler(on_mytasks_callback, pattern=r"^myt:"))
    app.add_handler(CallbackQueryHandler(on_set_shift_schedule_market_choice, pattern=r"^shsched_market:"))
    app.add_handler(CallbackQueryHandler(on_set_shift_schedule_confirm, pattern=r"^shsched_confirm$"))
    app.add_handler(CallbackQueryHandler(on_set_shift_schedule_cancel, pattern=r"^shsched_cancel$"))
    app.add_handler(CallbackQueryHandler(on_register_report_chat_market, pattern=r"^shrc_market:"))
    app.add_handler(CallbackQueryHandler(on_register_report_chat_role, pattern=r"^shrc_role:"))
    app.add_handler(CallbackQueryHandler(on_report_chats_list, pattern=r"^shrc_list$"))
    app.add_handler(CallbackQueryHandler(on_report_chats_view, pattern=r"^shrc_view:"))
    app.add_handler(CallbackQueryHandler(on_report_chats_unbind, pattern=r"^shrc_unbind:"))
    app.add_handler(CallbackQueryHandler(on_shift_report_manual_market_choice, pattern=r"^shrep_manualmarket:"))
    app.add_handler(CallbackQueryHandler(on_send_shift_report_market_choice, pattern=r"^shrep_sendmarket:"))
    app.add_handler(CallbackQueryHandler(on_reset_shift_report_market_choice, pattern=r"^shrep_resetmarket:"))
    app.add_handler(CallbackQueryHandler(on_reset_shift_report_confirm, pattern=r"^shrep_resetconfirm:"))
    app.add_handler(CallbackQueryHandler(on_reset_shift_report_cancel, pattern=r"^shrep_resetcancel$"))
    app.add_handler(CallbackQueryHandler(on_shift_report_fill, pattern=r"^shrep_fill:"))
    app.add_handler(CallbackQueryHandler(on_shift_report_fix_field, pattern=r"^shrep_fixfield:"))
    app.add_handler(CallbackQueryHandler(on_shift_report_absent, pattern=r"^shrep_absent:"))
    app.add_handler(CallbackQueryHandler(on_shift_report_supervisor_approve, pattern=r"^shrep_svapprove:"))
    app.add_handler(CallbackQueryHandler(on_shift_report_owner_approve, pattern=r"^shrep_ownapprove:"))
    app.add_handler(CallbackQueryHandler(on_shift_report_edit_field, pattern=r"^shrep_editfield:"))
    app.add_handler(CallbackQueryHandler(on_shift_report_edit_cancel, pattern=r"^shrep_editcancel:"))
    app.add_handler(CallbackQueryHandler(on_shift_report_edit, pattern=r"^shrep_edit:"))
    app.add_handler(CallbackQueryHandler(on_shift_report_more_info, pattern=r"^shrep_moreinfo:"))

    app.job_queue.run_daily(_status_check_job, time=_parse_time(STATUS_CHECK_TIME, TZ))
    app.job_queue.run_daily(_daily_report_job, time=_parse_time(DAILY_REPORT_TIME, TZ))
    app.job_queue.run_daily(
        _weekly_report_job,
        time=_parse_time(WEEKLY_REPORT_TIME, TZ),
        days=(_WEEKDAYS[WEEKLY_REPORT_DAY.lower()],),
    )
    app.job_queue.run_daily(_monitoring_reminder_job, time=_parse_time(MONITORING_REMINDER_TIME, TZ))
    app.job_queue.run_daily(_task_retention_job, time=_parse_time(STATUS_CHECK_TIME, TZ))
    app.job_queue.run_daily(_shift_schedule_reminder_job, time=_parse_time(SHIFT_SCHEDULE_REMINDER_TIME, TZ))
    app.job_queue.run_daily(_shift_report_kickoff_job, time=_parse_time(SHIFT_REPORT_START_TIME, TZ))
    app.job_queue.run_daily(_shift_report_escalate_job, time=_parse_time(SHIFT_REPORT_ESCALATE_TIME, TZ))
    app.job_queue.run_daily(_shift_report_dispatch_job, time=_parse_time(SHIFT_REPORT_DISPATCH_TIME, TZ))

    app.run_polling()


if __name__ == "__main__":
    main()
