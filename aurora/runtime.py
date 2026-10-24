import asyncio
from collections import defaultdict

from google.adk.agents.run_config import RunConfig
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

from .agents import build_agents
from .storage import Store
from .tools import approval

APP_NAME = "aurora"
USER_ID = "morador"


class Runtime:
    def __init__(self, settings, model=None):
        self.store = Store(settings)
        self.sessions = DatabaseSessionService(
            db_url=f"sqlite+aiosqlite:///{settings.state_dir / 'sessoes.db'}"
        )
        self.runner = Runner(
            app=App(
                name=APP_NAME,
                root_agent=build_agents(settings, self.store, model),
                resumability_config=ResumabilityConfig(is_resumable=True),
            ),
            session_service=self.sessions,
        )
        self.locks = defaultdict(asyncio.Lock)

    async def close(self):
        await self.runner.close()
        await self.sessions.close()

    async def create_session(self, apartment):
        if not self.store.apartment_exists(apartment):
            raise ValueError("Apartamento inexistente.")
        session = await self.sessions.create_session(
            app_name=APP_NAME, user_id=USER_ID, state={"apartamento": apartment}
        )
        self.store.bind(session.id, apartment)
        return session.id

    async def session(self, session_id):
        if self.store.apartment(session_id) is None:
            return None
        return await self.sessions.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )

    async def events(self, session_id):
        session = await self.session(session_id)
        return [e.model_dump(mode="json", exclude_none=True) for e in session.events]

    async def run(self, session_id, message, invocation_id=None):
        texts = []
        confirmed_tool_ran = False
        grant = approval.get()
        async for event in self.runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=message,
            invocation_id=invocation_id,
            run_config=RunConfig(max_llm_calls=8),
        ):
            for response in event.get_function_responses():
                if grant and response.id == grant["tool_id"]:
                    confirmed_tool_ran = True
            for call in event.get_function_calls():
                if call.name == "adk_request_confirmation":
                    original = call.args["originalFunctionCall"]
                    tool_confirmation = call.args.get("toolConfirmation", {})
                    self.store.add_confirmation(
                        id=call.id,
                        session_id=session_id,
                        invocation_id=event.invocation_id,
                        tool_id=original["id"],
                        acao=original["name"],
                        detalhes=tool_confirmation.get("payload") or original.get("args", {}),
                    )
            if event.content and not event.partial:
                for part in event.content.parts or []:
                    if part.text and not part.thought:
                        texts.append(part.text)
        # Uma aprovação aceita sem retomar a tool é uma falha, nunca um sucesso silencioso.
        if grant and not confirmed_tool_ran:
            raise RuntimeError("O ADK não retomou a tool que solicitou a confirmação.")
        return {
            "resposta": "\n".join(texts),
            "confirmacoes_pendentes": self.store.pending(session_id),
        }

    async def message(self, session_id, text):
        async with self.locks[session_id]:
            pending = self.store.pending(session_id)
            if pending:
                return {
                    "resposta": "Responda às confirmações pendentes pela rota de confirmações antes de continuar.",
                    "confirmacoes_pendentes": pending,
                }
            return await self.run(
                session_id, types.Content(role="user", parts=[types.Part(text=text)])
            )

    async def confirm(self, session_id, id, confirmed):
        async with self.locks[session_id]:
            item = self.store.claim_confirmation(session_id, id)
            if item is None:
                return None
            token = approval.set(item)
            try:
                message = types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=id,
                                name="adk_request_confirmation",
                                response={"confirmed": confirmed},
                            )
                        )
                    ],
                )
                result = await self.run(session_id, message, item["invocation_id"])
                self.store.finish_confirmation(id)
                return result
            finally:
                approval.reset(token)
