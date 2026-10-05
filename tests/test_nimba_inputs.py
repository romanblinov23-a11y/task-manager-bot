import pytest

from revenue.surfcoffee_client import (AWARDS_SHIFTS_URL, STAFF_NORM_URL, TEAM_URL, TIMETABLE_URL, SurfCoffeeClient)


class _Resp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200
        self.text = str(payload)

    def json(self):
        return self._p

    def raise_for_status(self):
        return None


class _Http:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.posts = []
        self.gets = []

    def get(self, url, params=None):
        self.gets.append((url, params))
        return self.get_responses.pop(0)

    def post(self, url, json=None):
        self.posts.append((url, json))
        return self.post_responses.pop(0)


def _client(http):
    c = SurfCoffeeClient("x", "y")
    c._client.close()
    c._client = http
    c._logged_in = True
    c.switch_spot = lambda spot_id: None
    return c


def test_awards_shifts_checks_period():
    http = _Http(get_responses=[_Resp({"success": True, "data": {"period": {"date": "2026-09-01"}, "awards": []}})])
    data = _client(http).get_awards_shifts("park_gorkogo", "2026-09")
    assert data["period"]["date"] == "2026-09-01"
    assert http.gets == [(AWARDS_SHIFTS_URL, {"date": "2026-09"})]


def test_awards_shifts_wrong_period_raises():
    http = _Http(get_responses=[_Resp({"success": True, "data": {"period": {"date": "2026-08-01"}}})])
    with pytest.raises(RuntimeError):
        _client(http).get_awards_shifts("park_gorkogo", "2026-09")


def test_team_paginates_until_last_page():
    p1 = {"success": True, "data": {"employees": [{"id": 1}], "meta": {"current_page": 1, "last_page": 2}}}
    p2 = {"success": True, "data": {"employees": [{"id": 2}], "meta": {"current_page": 2, "last_page": 2}}}
    http = _Http(post_responses=[_Resp(p1), _Resp(p2)])
    team = _client(http).get_team("park_gorkogo")
    assert [e["id"] for e in team["employees"]] == [1, 2]
    assert [b[1]["page"] for b in http.posts] == [1, 2]
    assert all(url == TEAM_URL for url, _ in http.posts)


def test_staff_norm_returns_data():
    http = _Http(get_responses=[_Resp({"success": True, "data": {"staff_norm_value": 35, "staff_actual_value": 28}})])
    data = _client(http).get_staff_norm("park_gorkogo")
    assert data["staff_norm_value"] == 35
    assert http.gets == [(STAFF_NORM_URL, None)]


def test_timetable_checks_first_day_of_month():
    ok = {"success": True, "data": {"results": {"dates": [{"01.09.2026": 115}]}, "spot_employees": {}}}
    http = _Http(post_responses=[_Resp(ok)])
    data = _client(http).get_timetable("park_gorkogo", "2026-09")
    assert "spot_employees" in data
    assert http.posts == [(TIMETABLE_URL, {"date": "2026-09"})]


def test_timetable_wrong_month_raises():
    other = {"success": True, "data": {"results": {"dates": [{"01.08.2026": 1}]}}}
    http = _Http(post_responses=[_Resp(other)])
    with pytest.raises(RuntimeError):
        _client(http).get_timetable("park_gorkogo", "2026-09")
