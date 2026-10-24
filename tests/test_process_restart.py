import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import httpx


def test_real_process_restart_restores_events_and_pending_approval(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(
        os.environ,
        AURORA_STATE_DIR=str(tmp_path / "state"),
        GOOGLE_API_KEY="contract-test-key",
        GOOGLE_GENAI_USE_VERTEXAI="FALSE",
        GEMINI_SIM_URL=os.getenv("GEMINI_SIM_URL", "http://127.0.0.1:4900/gemini"),
    )

    @contextmanager
    def running():
        with (tmp_path / "process.log").open("a") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "sim_app:app",
                    "--app-dir",
                    "tests",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                env=env,
                stdout=log,
                stderr=log,
            )
            client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15)
            try:
                for _ in range(100):
                    assert process.poll() is None, (tmp_path / "process.log").read_text()
                    try:
                        if client.get("/openapi.json").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.05)
                else:
                    raise AssertionError("API não iniciou.")
                yield client
            finally:
                client.close()
                process.terminate()
                process.wait(timeout=10)

    with running() as client:
        s = client.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
        response = client.post(
            f"/sessoes/{s}/mensagens",
            json={
                "texto": "SIM:"
                + json.dumps(
                    {"tool": "reservar", "args": {"area": "salao-de-festas", "data": "2030-07-01"}}
                )
            },
        )
        assert response.status_code == 200, response.text
        pending = response.json()["confirmacoes_pendentes"][0]
        before = client.get(f"/sessoes/{s}/eventos").json()
    with running() as client:
        assert client.get(f"/sessoes/{s}/eventos").json() == before
        response = client.post(
            f"/sessoes/{s}/confirmacoes", json={"id": pending["id"], "confirmado": True}
        )
        assert response.status_code == 200, response.text
        assert (
            sum(r["data"] == "2030-07-01" for r in client.get("/apartamentos/101/reservas").json())
            == 1
        )
        after = client.get(f"/sessoes/{s}/eventos").json()
        assert len(after) > len(before)
    with running() as client:
        assert client.get(f"/sessoes/{s}/eventos").json() == after
        assert (
            sum(r["data"] == "2030-07-01" for r in client.get("/apartamentos/101/reservas").json())
            == 1
        )
        assert (
            client.post(
                f"/sessoes/{s}/confirmacoes", json={"id": pending["id"], "confirmado": True}
            ).status_code
            == 409
        )
