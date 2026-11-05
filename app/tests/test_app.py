import main
from fastapi.testclient import TestClient

client = TestClient(main.app)


def test_health_returns_200():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_ok_when_db_up(monkeypatch):
    monkeypatch.setattr(main, "db_ping", lambda: None)
    assert client.get("/ready").status_code == 200


def test_ready_503_when_db_down(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(main, "db_ping", boom)
    assert client.get("/ready").status_code == 503


def test_create_and_list_items(monkeypatch):
    store = []

    def fake_create(name):
        item = {"id": len(store) + 1, "name": name}
        store.append(item)
        return item

    monkeypatch.setattr(main, "db_create_item", fake_create)
    monkeypatch.setattr(main, "db_list_items", lambda: store)

    r = client.post("/items", json={"name": "buy milk"})
    assert r.status_code == 201
    assert client.get("/items").json() == [{"id": 1, "name": "buy milk"}]


def test_metrics_endpoint_exposes_counters():
    client.get("/health")
    body = client.get("/metrics").text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
