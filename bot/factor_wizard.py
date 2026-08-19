from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def field_keyboard(field: tuple, callback_prefix: str) -> InlineKeyboardMarkup | None:
    _, _, kind, options, _ = field
    if kind != "buttons":
        return None
    buttons = []
    for i in range(0, len(options), 2):
        row = [InlineKeyboardButton(options[i], callback_data=f"{callback_prefix}:{i}")]
        if i + 1 < len(options):
            row.append(InlineKeyboardButton(options[i + 1], callback_data=f"{callback_prefix}:{i + 1}"))
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def field_prompt_text(block_title: str, field: tuple, is_new_block: bool) -> str:
    _, label, _, _, note = field
    prefix = f"Блок «{block_title}».\n\n" if is_new_block else ""
    text = f"{prefix}{label}:"
    if note:
        text += f"\n({note})"
    return text
