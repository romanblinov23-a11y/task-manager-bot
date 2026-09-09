"""
Генерация аналитических комментариев по выручке через Claude.

Берёт собранные цифры по точке (или по сети) и просит Claude дать короткий,
содержательный комментарий о динамике, причинах и рисках — без пересказа
самих цифр (они уже показаны в предыдущем сообщении).

Перенесено из бота "Аналитик Иван" (claude_analyst.py) — единственное
изменение: вместо своего httpx-запроса к Anthropic использует уже
настроенный клиент Енисея (prompts.client.ask_claude), поэтому не нужен
свой api_key и не задан отдельно MODEL (берётся ANTHROPIC_MODEL Енисея).
"""

from prompts.client import ask_claude

SYSTEM_PROMPT = """Ты — финансовый аналитик сети кофеен Surf Coffee в Москве.
Твоя задача — дать ёмкий комментарий строго в 2-3 коротких предложения.

Правила:
- НЕ повторяй цифры — читатель их уже видел
- Только суть: что происходит, почему (день недели, сезон), есть ли риск по плану
- Никакой воды, никаких общих фраз
- Обычный текст, без markdown
"""


class ClaudeCommentary:
    def _ask(self, user_message: str) -> str:
        return ask_claude(user_message, max_tokens=180, system=SYSTEM_PROMPT).strip()

    def analyze_spot_day(
        self,
        spot_title: str,
        yesterday_fact: float,
        yesterday_plan: float,
        week_ago_fact: float | None,
        mtd_fact: float,
        mtd_plan: float,
        mtd_last_year_fact: float | None,
        forecast: float,
        yesterday_count: int | None = None,
        week_ago_count: int | None = None,
        yesterday_average: float | None = None,
        week_ago_average: float | None = None,
    ) -> str:
        """Аналитика по одной точке за день + месяц (используется в ежедневном отчёте)."""
        lines = [
            f"Точка: {spot_title}",
            f"Вчера: факт {yesterday_fact:.0f} руб, план {yesterday_plan:.0f} руб",
        ]
        if yesterday_count is not None:
            lines.append(f"Чеков вчера: {yesterday_count}")
        if yesterday_average is not None:
            lines.append(f"Средний чек вчера: {yesterday_average:.0f} руб")

        if week_ago_fact:
            lines.append(f"Тот же день неделю назад — выручка: {week_ago_fact:.0f} руб")
        if week_ago_count is not None:
            lines.append(f"Тот же день неделю назад — чеков: {week_ago_count}")
        if week_ago_average is not None:
            lines.append(f"Тот же день неделю назад — средний чек: {week_ago_average:.0f} руб")

        lines.append(f"С начала месяца: факт {mtd_fact:.0f} руб, план {mtd_plan:.0f} руб")
        if mtd_last_year_fact:
            lines.append(f"Тот же период прошлого года: {mtd_last_year_fact:.0f} руб")
        else:
            lines.append("Данных за прошлый год нет")
        lines.append(f"Прогноз на конец месяца: {forecast:.0f} руб")
        lines.append(
            "\nДай короткий аналитический комментарий по этим данным. Если есть данные "
            "по чекам и среднему чеку — учти их: например, падение выручки при росте "
            "среднего чека и падении числа чеков говорит о потере трафика, а не о падении спроса."
        )

        return self._ask("\n".join(lines))

    def analyze_spot_week(
        self,
        spot_title: str,
        week_from: str,
        week_to: str,
        week_fact: float,
        week_plan: float,
        prev_week_fact: float | None,
        same_week_last_year_fact: float | None,
        week_count: int | None = None,
        prev_week_count: int | None = None,
        week_average: float | None = None,
        prev_week_average: float | None = None,
    ) -> str:
        """Аналитика по одной точке за ЦЕЛУЮ НЕДЕЛЮ (понедельничный отчёт)."""
        lines = [
            f"Точка: {spot_title}",
            f"Это итог ЗА ПРОШЕДШУЮ НЕДЕЛЮ целиком ({week_from} - {week_to}), не за один день.",
            f"Факт за неделю: {week_fact:.0f} руб, план за неделю: {week_plan:.0f} руб",
        ]
        if week_count is not None:
            lines.append(f"Чеков за неделю: {week_count}")
        if week_average is not None:
            lines.append(f"Средний чек за неделю: {week_average:.0f} руб")

        if prev_week_fact:
            lines.append(f"Предыдущая неделя (тоже целиком) — выручка: {prev_week_fact:.0f} руб")
        else:
            lines.append("Данных за предыдущую неделю нет")
        if prev_week_count is not None:
            lines.append(f"Предыдущая неделя — чеков: {prev_week_count}")
        if prev_week_average is not None:
            lines.append(f"Предыдущая неделя — средний чек: {prev_week_average:.0f} руб")

        if same_week_last_year_fact:
            lines.append(f"Та же неделя год назад: {same_week_last_year_fact:.0f} руб")
        else:
            lines.append("Данных за ту же неделю год назад нет")
        lines.append(
            "\nДай короткий аналитический комментарий по итогам этой недели: "
            "динамика по сравнению с предыдущей неделей, риски/успехи по плану. "
            "Если есть данные по чекам и среднему чеку — обязательно используй их: "
            "это позволяет понять, изменилась ли выручка из-за трафика или из-за среднего чека. "
            "Не путай это с дневными данными — речь идёт о сумме за все 7 дней недели."
        )

        return self._ask("\n".join(lines))

    def analyze_spot_month(
        self,
        spot_title: str,
        month_title: str,
        month_fact: float,
        month_plan: float,
        prev_month_fact: float | None,
        same_month_last_year_fact: float | None,
        month_count: int | None = None,
        prev_month_count: int | None = None,
        month_average: float | None = None,
        prev_month_average: float | None = None,
    ) -> str:
        """Аналитика по одной точке за ЦЕЛЫЙ ЗАКОНЧИВШИЙСЯ МЕСЯЦ (отчёт 1-го числа)."""
        lines = [
            f"Точка: {spot_title}",
            f"Это итог ЗА ПОЛНОСТЬЮ ЗАКОНЧИВШИЙСЯ МЕСЯЦ {month_title}, не за один день и не за неделю.",
            f"Факт за месяц: {month_fact:.0f} руб, план за месяц: {month_plan:.0f} руб",
        ]
        if month_count is not None:
            lines.append(f"Чеков за месяц: {month_count}")
        if month_average is not None:
            lines.append(f"Средний чек за месяц: {month_average:.0f} руб")

        if prev_month_fact:
            lines.append(f"Предыдущий месяц (тоже целиком) — выручка: {prev_month_fact:.0f} руб")
        else:
            lines.append("Данных за предыдущий месяц нет")
        if prev_month_count is not None:
            lines.append(f"Предыдущий месяц — чеков: {prev_month_count}")
        if prev_month_average is not None:
            lines.append(f"Предыдущий месяц — средний чек: {prev_month_average:.0f} руб")

        if same_month_last_year_fact:
            lines.append(f"Тот же месяц год назад: {same_month_last_year_fact:.0f} руб")
        else:
            lines.append("Данных за тот же месяц год назад нет")
        lines.append(
            "\nДай короткий аналитический комментарий по итогам этого месяца целиком: "
            "выполнен ли план, как это соотносится с предыдущим месяцем и аналогичным "
            "периодом прошлого года, главные выводы. Месяц уже полностью завершён, "
            "прогнозировать дальше нечего — речь об итоговой оценке. Если есть данные "
            "по чекам и среднему чеку — обязательно используй их в анализе: это позволяет "
            "понять, что произошло с трафиком, а что — со средним чеком."
        )

        return self._ask("\n".join(lines))

    def analyze_network(self, spot_summaries: list[dict]) -> str:
        """
        Аналитика по всей сети.
        spot_summaries: [{"title": ..., "fact": ..., "plan": ...}, ...]
        """
        lines = ["Сводка по сети Surf Coffee (3 точки), вчерашний день:"]
        for s in spot_summaries:
            lines.append(f"- {s['title']}: факт {s['fact']:.0f} руб, план {s['plan']:.0f} руб")
        lines.append("\nДай короткий аналитический комментарий по сети целиком: "
                      "какие точки тянут вверх/вниз, общая картина.")
        return self._ask("\n".join(lines))

    def analyze_network_period(self, spot_summaries: list[dict], period_label: str) -> str:
        """
        Аналитика по сети за произвольный период (неделя/месяц) — period_label
        явно указывает Claude, что это не один день, например:
        "прошедшая неделя 22.06-28.06.2026" или "месяц июнь 2026".
        """
        lines = [f"Сводка по сети Surf Coffee (3 точки) за {period_label} (это период целиком, не один день):"]
        for s in spot_summaries:
            lines.append(f"- {s['title']}: факт {s['fact']:.0f} руб, план {s['plan']:.0f} руб")
        lines.append(
            f"\nДай короткий аналитический комментарий по сети целиком за весь этот период: "
            "какие точки тянут вверх/вниз, общая картина по плану."
        )
        return self._ask("\n".join(lines))
