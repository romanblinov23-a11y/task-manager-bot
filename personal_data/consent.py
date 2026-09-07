from personal_data.db import get_connection

# Версия текста согласия — при изменении формулировки увеличивать явно
# (v1, v2, ...), чтобы для каждого стажёра было видно, на какой именно
# текст он согласился.
CONSENT_VERSION = "v1"

# ⚠️ ЧЕРНОВИК. Текст ниже — рабочая заготовка, не финальная юридическая
# формулировка: не хватает точных реквизитов оператора персональных данных
# и части обязательных по ст.9 152-ФЗ пунктов (точный перечень действий,
# срок хранения и т.п.). Перед реальным использованием должен быть
# проверен юристом.
CONSENT_TEXT = (
    "📄 Согласие на обработку персональных данных\n\n"
    "Перед началом стажировки нужно твоё согласие на обработку персональных данных "
    "(ФИО, телефон, паспортные данные, СНИЛС, банковские реквизиты) в соответствии "
    "с 152-ФЗ «О персональных данных».\n\n"
    "Данные нужны для оформления трудовых отношений и используются только для этого — "
    "начисление зарплаты, оформление документов, связь по рабочим вопросам.\n\n"
    "Нажимая «✅ Согласен(на)», ты подтверждаешь согласие на сбор, хранение и обработку "
    "этих данных."
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


def record_consent(telegram_user_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO consent (telegram_user_id, consent_version, revoked_at) VALUES (?, ?, NULL)
            ON CONFLICT (telegram_user_id) DO UPDATE SET
                consent_version = excluded.consent_version, agreed_at = datetime('now'), revoked_at = NULL
            """,
            (telegram_user_id, CONSENT_VERSION),
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
