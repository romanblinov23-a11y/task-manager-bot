from personal_data.db import get_connection

# ⚠️ ЧЕРНОВИК. Шаблон ниже — рабочая заготовка, не финальная юридическая
# формулировка: часть обязательных по ст.9 152-ФЗ пунктов (точный перечень
# действий, срок хранения и т.п.) может потребовать уточнения юристом.
# Оператором персональных данных подставляется конкретное юрлицо/ИП точки
# (market.operator_*, см. /set_operator) — не Роман и не бот, поскольку
# именно это юрлицо реально владеет точкой по договору оказания услуг.
_CONSENT_TEMPLATE = (
    "📄 Согласие на обработку персональных данных\n\n"
    "Перед началом стажировки нужно твоё согласие на обработку персональных данных "
    "(ФИО, телефон, паспортные данные, СНИЛС, банковские реквизиты) в соответствии "
    "с 152-ФЗ «О персональных данных».\n\n"
    "Оператор персональных данных: {operator_name}, ИНН {operator_inn}, ОГРН(ИП) {operator_ogrn}, "
    "адрес: {operator_address}.\n\n"
    "Данные нужны для оформления трудовых отношений и используются только для этого — "
    "начисление зарплаты, оформление документов, связь по рабочим вопросам.\n\n"
    "Нажимая «✅ Согласен(на)», ты подтверждаешь согласие на сбор, хранение и обработку "
    "этих данных оператором, указанным выше."
)


def render_consent_text(market: dict) -> str:
    """Если для рынка загружен готовый текст от юристов оператора
    (market.custom_consent_text, см. /set_consent_text) — используется он
    как есть, без подстановок. Иначе — автогенерация по реквизитам
    оператора (/set_operator)."""
    custom = (market.get("custom_consent_text") or "").strip()
    if custom:
        return custom
    return _CONSENT_TEMPLATE.format(
        operator_name=market.get("operator_name") or "—",
        operator_inn=market.get("operator_inn") or "—",
        operator_ogrn=market.get("operator_ogrn") or "—",
        operator_address=market.get("operator_address") or "—",
    )


def has_consent(telegram_user_id: int) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM consent WHERE telegram_user_id = ? AND revoked_at IS NULL", (telegram_user_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def record_consent(telegram_user_id: int, market_id: int, consent_text: str) -> None:
    """Хранит не абстрактную «версию», а сам текст, под которым человек
    поставил согласие, — вместе с market_id (юрлицо-оператор мог быть
    показан только в этом тексте) так проще всего доказать постфактум,
    на что именно кто-то согласился, даже если реквизиты юрлица потом
    изменятся."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO consent (telegram_user_id, market_id, consent_text, revoked_at) VALUES (?, ?, ?, NULL)
            ON CONFLICT (telegram_user_id) DO UPDATE SET
                market_id = excluded.market_id, consent_text = excluded.consent_text,
                agreed_at = datetime('now'), revoked_at = NULL
            """,
            (telegram_user_id, market_id, consent_text),
        )
        conn.commit()
    finally:
        conn.close()


def revoke_consent(telegram_user_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE consent SET revoked_at = datetime('now') WHERE telegram_user_id = ?", (telegram_user_id,))
        conn.commit()
    finally:
        conn.close()
