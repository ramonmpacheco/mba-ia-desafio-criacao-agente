from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from google.genai.errors import APIError
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .config import Settings
from .runtime import Runtime


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionInput(Input):
    apartamento: str = Field(min_length=1)


class MessageInput(Input):
    texto: str = Field(min_length=1)


class ConfirmationInput(Input):
    id: str = Field(min_length=1)
    confirmado: StrictBool


class Pending(BaseModel):
    id: str
    acao: str
    detalhes: dict


class Reply(BaseModel):
    resposta: str
    confirmacoes_pendentes: list[Pending]


class Created(BaseModel):
    session_id: str


class Reservation(BaseModel):
    codigo: str
    area: str
    data: str


class Visitor(BaseModel):
    nome: str
    data: str


def create_app(settings=None, model=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.runtime = Runtime(settings or Settings.load(), model)
        try:
            yield
        finally:
            await app.state.runtime.close()

    app = FastAPI(title="Residencial Aurora", version="1.0.0", lifespan=lifespan)

    async def require_session(id):
        if await app.state.runtime.session(id) is None:
            raise HTTPException(404, "Sessão inexistente.")

    @app.post("/sessoes", status_code=201, response_model=Created)
    async def create_session(body: SessionInput):
        try:
            id = await app.state.runtime.create_session(body.apartamento)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"session_id": id}

    @app.post(
        "/sessoes/{session_id}/mensagens",
        response_model=Reply,
        responses={404: {"description": "Sessão inexistente"}},
    )
    async def message(session_id: str, body: MessageInput):
        await require_session(session_id)
        try:
            return await app.state.runtime.message(session_id, body.texto)
        except APIError as exc:
            raise HTTPException(
                503, "Gemini indisponível. Verifique modelo, chave e cota do projeto."
            ) from exc

    @app.post(
        "/sessoes/{session_id}/confirmacoes",
        response_model=Reply,
        responses={
            404: {"description": "Sessão inexistente"},
            409: {"description": "Confirmação não pendente nesta sessão"},
        },
    )
    async def confirm(session_id: str, body: ConfirmationInput):
        await require_session(session_id)
        try:
            result = await app.state.runtime.confirm(session_id, body.id, body.confirmado)
        except APIError as exc:
            raise HTTPException(
                503, "Gemini indisponível. Verifique modelo, chave e cota do projeto."
            ) from exc
        if result is None:
            raise HTTPException(409, "Confirmação não pendente nesta sessão.")
        return result

    @app.get(
        "/sessoes/{session_id}/eventos",
        response_model=list[dict],
        responses={404: {"description": "Sessão inexistente"}},
    )
    async def events(session_id: str):
        await require_session(session_id)
        return await app.state.runtime.events(session_id)

    @app.get("/apartamentos/{apartamento}/reservas", response_model=list[Reservation])
    async def reservations(apartamento: str):
        return app.state.runtime.store.reservations(apartamento)

    @app.get("/apartamentos/{apartamento}/visitantes", response_model=list[Visitor])
    async def visitors(apartamento: str):
        return app.state.runtime.store.visitors(apartamento)

    return app
