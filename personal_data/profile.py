from personal_data.db import get_connection

# Поля профиля — то, что дальше пойдёт в отдельный сбор (паспорт/СНИЛС/
# реквизиты для выплат). Сама анкета сбора этих полей — следующий шаг,
# после того как текст согласия пройдёт проверку юриста и будет решено,
# как именно хранить сканы документов (см. personal_data/consent.py).
FIELDS = (
    "phone",
    "passport_series_number",
    "passport_issued_by",
    "passport_issued_date",
    "snils",
    "bank_account",
    "bank_bic",
    "bank_name",
)


def get_profile(telegram_user_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM trainee_profile WHERE telegram_user_id = ?", (telegram_user_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def set_profile_field(telegram_user_id: int, field: str, value: str) -> None:
    if field not in FIELDS:
        raise ValueError(f"Неизвестное поле профиля стажёра: {field}")
    conn = get_connection()
    try:
        conn.execute(
            f"""
            INSERT INTO trainee_profile (telegram_user_id, {field}, updated_at) VALUES (?, ?, datetime('now'))
            ON CONFLICT (telegram_user_id) DO UPDATE SET {field} = excluded.{field}, updated_at = datetime('now')
            """,
            (telegram_user_id, value),
        )
        conn.commit()
    finally:
        conn.close()
