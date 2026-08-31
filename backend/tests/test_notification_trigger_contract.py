from app.domains.planning import notification_strings, notifications


def test_routine_push_action_data_is_allowlisted_and_keeps_server_owned_date():
    destination, params = notifications._target(
        "/(tabs)/care",
        {
            "routine_action": "complete_step",
            "step_id": "11111111-1111-1111-1111-111111111111",
            "done_on": "2026-08-31",
            "category_id": "client-controlled-value",
            "other": "discarded",
        },
    )
    assert destination == "/(tabs)/care"
    assert params == {
        "routine_action": "complete_step",
        "step_id": "11111111-1111-1111-1111-111111111111",
        "done_on": "2026-08-31",
        "category_id": "routine-adherence",
    }


def test_new_notification_copy_describes_account_facts_without_health_claims():
    copy = " ".join(
        value for key, value in vars(notification_strings).items()
        if key.isupper() and isinstance(value, str)
    )
    for prohibited in ("diagnos", "treat", "cure", "prevent", "health benefit"):
        assert prohibited not in copy.lower()
