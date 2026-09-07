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


def set_profile_field(telegram_user_id: int, market_id: int, field: str, value: str) -> None:
    """market_id записывается только при создании строки (первый вызов для
    этого telegram_user_id) — нужен для выгрузки/удаления данных по рынку
    при завершении договора на управление (см. bot.data_export)."""
    if field not in FIELDS:
        raise ValueError(f"Неизвестное поле профиля стажёра: {field}")
    conn = get_connection()
    try:
        conn.execute(
            f"""
            INSERT INTO trainee_profile (telegram_user_id, market_id, {field}, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT (telegram_user_id) DO UPDATE SET {field} = excluded.{field}, updated_at = datetime('now')
            """,
            (telegram_user_id, market_id, value),
        )
        conn.commit()
    finally:
        conn.close()


def list_profiles_for_market(market_id: int) -> list[dict]:
    """Все анкеты стажёров/сотрудников этого рынка — для выгрузки/удаления
    персональных данных при передаче точки заказчику (см. bot.data_export)."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM trainee_profile WHERE market_id = ?", (market_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_profiles_for_market(market_id: int) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM trainee_profile WHERE market_id = ?", (market_id,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
