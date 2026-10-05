import pytest

from revenue.surfcoffee_client import MANAGER_REPORT_URL, SurfCoffeeClient


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeHttp:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        return self._responses.pop(0)


def _client(responses):
    client = SurfCoffeeClient("x@y", "z")
    client._client.close()
    client._client = _FakeHttp(responses)
    client._logged_in = True
    client.switch_spot = lambda spot_id: None
    return client


def _report(actual_date):
    return {"success": True, "data": {"actual_date": actual_date, "fin_result": [], "revenue_expenses": [], "dividends": []}}


def test_returns_report_when_period_matches():
    client = _client([_FakeResponse(_report("01.09.2026"))])
    data = client.get_manager_report("park_gorkogo", "2026-09")
    assert data["actual_date"] == "01.09.2026"
    assert client._client.calls == [(MANAGER_REPORT_URL, {"date": "2026-09"})]


def test_retries_when_server_returns_other_month():
    client = _client([_FakeResponse(_report("01.08.2026")), _FakeResponse(_report("01.09.2026"))])
    data = client.get_manager_report("park_gorkogo", "2026-09")
    assert data["actual_date"] == "01.09.2026"
    assert len(client._client.calls) == 2


def test_raises_when_period_never_matches():
    client = _client([_FakeResponse(_report("01.08.2026")), _FakeResponse(_report("01.08.2026"))])
    with pytest.raises(RuntimeError, match="01.09.2026"):
        client.get_manager_report("park_gorkogo", "2026-09")


def test_guests_report_returns_when_requested_month_present():
    body = '{"success": true, "data": {"guests": [{"date": "2026-09-01"}], "receipts": []}}'
    resp = _FakeResponse({"success": True, "data": {"guests": [{"date": "2026-09-01"}], "receipts": []}})
    resp.text = body
    client = _client([resp])
    data = client.get_guests_report("park_gorkogo", "2026-09")
    assert data["guests"][0]["date"] == "2026-09-01"


def test_guests_report_retries_then_fails_when_month_missing():
    old = _FakeResponse({"success": True, "data": {}})
    old.text = '{"success": true, "data": {"guests": [{"date": "2026-08-01"}]}}'
    client = _client([old, old])
    with pytest.raises(RuntimeError, match="2026-09-01"):
        client.get_guests_report("park_gorkogo", "2026-09")
