from monitoring.factor_schema import (
    apply_changes_to_factors,
    parse_block_value,
    serialize_block,
    validate_proposed_changes,
)


def test_validate_proposed_changes_accepts_valid_button_value():
    raw = [{"block": "atmosphere", "field": "music", "new_value": "приятная", "reason": "новая музыка"}]
    result = validate_proposed_changes(raw, None)
    assert len(result) == 1
    assert result[0] == {
        "block_key": "atmosphere",
        "field_key": "music",
        "label": "Музыка",
        "old_value": "—",
        "new_value": "приятная",
        "reason": "новая музыка",
    }


def test_validate_proposed_changes_rejects_value_outside_options():
    raw = [{"block": "atmosphere", "field": "music", "new_value": "громкая", "reason": "..."}]
    assert validate_proposed_changes(raw, None) == []


def test_validate_proposed_changes_rejects_unknown_field():
    raw = [{"block": "atmosphere", "field": "not_a_real_field", "new_value": "x"}]
    assert validate_proposed_changes(raw, None) == []


def test_validate_proposed_changes_rejects_unknown_block():
    raw = [{"block": "not_a_block", "field": "music", "new_value": "приятная"}]
    assert validate_proposed_changes(raw, None) == []


def test_validate_proposed_changes_parses_number_field():
    raw = [{"block": "product", "field": "avg_check", "new_value": "350"}]
    result = validate_proposed_changes(raw, None)
    assert result[0]["new_value"] == 350


def test_validate_proposed_changes_rejects_non_numeric_for_number_field():
    raw = [{"block": "product", "field": "avg_check", "new_value": "много"}]
    assert validate_proposed_changes(raw, None) == []


def test_validate_proposed_changes_picks_up_old_value_from_existing_row():
    factors_row = {"atmosphere": serialize_block({"music": "нет"})}
    raw = [{"block": "atmosphere", "field": "music", "new_value": "приятная"}]
    result = validate_proposed_changes(raw, factors_row)
    assert result[0]["old_value"] == "нет"


def test_validate_proposed_changes_ignores_malformed_entries():
    raw = ["not a dict", {"block": "atmosphere"}, {"field": "music", "new_value": "приятная"}]
    assert validate_proposed_changes(raw, None) == []


def test_apply_changes_to_factors_preserves_other_fields_in_same_block():
    factors_row = {"atmosphere": serialize_block({"music": "нет", "cleanliness": "чисто"})}
    changes = [{"block_key": "atmosphere", "field_key": "music", "new_value": "приятная"}]
    result = apply_changes_to_factors(factors_row, changes)
    updated = parse_block_value(result["atmosphere"])
    assert updated["music"] == "приятная"
    assert updated["cleanliness"] == "чисто"


def test_apply_changes_to_factors_preserves_other_blocks_untouched():
    factors_row = {"product": serialize_block({"grain": "смесь"}), "atmosphere": serialize_block({"music": "нет"})}
    changes = [{"block_key": "atmosphere", "field_key": "music", "new_value": "приятная"}]
    result = apply_changes_to_factors(factors_row, changes)
    assert parse_block_value(result["product"])["grain"] == "смесь"


def test_apply_changes_to_factors_handles_empty_starting_row():
    changes = [{"block_key": "labor_market", "field_key": "hourly_rate", "new_value": 500}]
    result = apply_changes_to_factors(None, changes)
    assert parse_block_value(result["labor_market"])["hourly_rate"] == 500
