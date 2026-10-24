import json

from fastapi.testclient import TestClient
from google.adk.models.google_llm import Gemini

from aurora.api import create_app


def request(client, session, tool, **args):
    response = client.post(
        f"/sessoes/{session}/mensagens",
        json={"texto": "SIM:" + json.dumps({"tool": tool, "args": args})},
    )
    assert response.status_code == 200, response.text
    return response.json()


def session(client, apartment="101"):
    response = client.post("/sessoes", json={"apartamento": apartment})
    assert response.status_code == 201
    return response.json()["session_id"]


def confirm(client, s, pending, confirmed=True):
    return client.post(
        f"/sessoes/{s}/confirmacoes", json={"id": pending["id"], "confirmado": confirmed}
    )


def test_confirmation_survives_runtime_restart(settings, model):
    with TestClient(create_app(settings, model)) as client:
        s = session(client)
        reply = request(client, s, "reservar", area="salao-de-festas", data="2030-04-20")
        assert len(reply["confirmacoes_pendentes"]) == 1
        pending = reply["confirmacoes_pendentes"][0]
        assert pending["detalhes"] == {
            "area": "salao-de-festas",
            "data": "2030-04-20",
            "taxa": 150.0,
        }
        assert len(client.get("/apartamentos/101/reservas").json()) == 1
        events = client.get(f"/sessoes/{s}/eventos").json()
    # Instância nova do App/Runner/DatabaseSessionService e cliente HTTP, sem memória anterior.
    fresh_model = Gemini(model=model.model, base_url=model.base_url)
    with TestClient(create_app(settings, fresh_model)) as client:
        assert client.get(f"/sessoes/{s}/eventos").json() == events
        response = confirm(client, s, pending)
        assert response.status_code == 200, response.text
        assert response.json()["confirmacoes_pendentes"] == []
        reservations = client.get("/apartamentos/101/reservas").json()
        assert sum(r["data"] == "2030-04-20" for r in reservations) == 1
        assert confirm(client, s, pending).status_code == 409
        assert confirm(client, s, {"id": "inexistente"}).status_code == 409
        assert client.get("/apartamentos/101/reservas").json() == reservations


def test_denial_and_cross_session(client):
    s = session(client)
    other = session(client, "201")
    reply = request(client, s, "autorizar_visitante", nome="Joana Ribeiro", data="2030-04-21")
    p = reply["confirmacoes_pendentes"][0]
    assert confirm(client, other, p).status_code == 409
    assert confirm(client, s, p, False).status_code == 200
    assert not client.get("/apartamentos/101/visitantes").json()
    assert confirm(client, s, p).status_code == 409


def test_free_reservation_and_own_cancel(client):
    s = session(client)
    result = request(client, s, "reservar", area="quadra", data="2030-04-06")
    assert not result["confirmacoes_pendentes"]
    request(client, s, "cancelar_reserva", area="quadra", data="2030-03-09")
    reservations = client.get("/apartamentos/101/reservas").json()
    assert len(reservations) == 1
    assert reservations[0]["data"] == "2030-04-06"
    result = request(client, s, "cancelar_reserva", area="salao-de-festas", data="2030-03-16")
    assert not result["confirmacoes_pendentes"]
    assert client.get("/apartamentos/302/reservas").json()[0]["codigo"] == "RSV-4821"
    events = json.dumps(client.get(f"/sessoes/{s}/eventos").json())
    assert "RSV-4821" not in events
    assert "Marina Duarte" not in events


def test_pool_retrieval_and_session_not_found(client):
    s = session(client)
    result = request(
        client,
        s,
        "consultar_regulamento",
        pergunta="Até que horas a piscina funciona aos domingos?",
    )
    assert "20h" in result["resposta"]
    events = json.dumps(client.get(f"/sessoes/{s}/eventos").json())
    assert "Art. 22" in events
    assert "Art. 8" not in events
    assert "Capítulo VIII" not in events
    assert client.get("/sessoes/inexistente/eventos").status_code == 404
    assert client.post("/sessoes/inexistente/mensagens", json={"texto": "Oi"}).status_code == 404
    assert confirm(client, "inexistente", {"id": "qualquer"}).status_code == 404
