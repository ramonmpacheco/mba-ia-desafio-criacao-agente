"""Tools de domínio: identidade e aprovação vêm do runtime, nunca do modelo."""

import contextvars
import re
import unicodedata
from datetime import date

from google.adk.tools import ToolContext

# Apenas a rota de confirmações estabelece essa autorização durante a execução.
approval = contextvars.ContextVar("aurora_approval", default=None)


def normalize(text):
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c)
    )


class Tools:
    def __init__(self, store):
        self.store = store

    def apartment(self, ctx):
        apartment = self.store.apartment(ctx.session.id)
        if apartment is None:
            raise RuntimeError("Sessão sem vínculo de apartamento.")
        return apartment

    def operation(self, ctx):
        return f"{ctx.session.id}:{ctx.function_call_id}"

    def gate(self, ctx, action, details):
        granted = approval.get()
        authorized = (
            granted is not None
            and granted["session_id"] == ctx.session.id
            and granted["tool_id"] == ctx.function_call_id
            and granted["acao"] == action
            and granted["detalhes"] == details
            and ctx.tool_confirmation is not None
            and ctx.tool_confirmation.confirmed
        )
        if authorized:
            return None
        if ctx.tool_confirmation is not None:
            return {"status": "negada", "mensagem": "Ação não aprovada pela rota de confirmações."}
        ctx.request_confirmation(hint="Confirme a ação pela rota de confirmações.", payload=details)
        ctx.actions.skip_summarization = True
        return {"status": "pendente", "acao": action, "detalhes": details}

    def listar_areas(self) -> list[dict]:
        """Lista IDs das áreas comuns, nomes e taxas, em reais."""
        return self.store.areas()

    def minhas_reservas(self, tool_context: ToolContext) -> list[dict]:
        """Consulta somente as reservas ativas do apartamento da sessão."""
        return self.store.reservations(self.apartment(tool_context))

    def meus_visitantes(self, tool_context: ToolContext) -> list[dict]:
        """Consulta somente os visitantes autorizados pelo apartamento da sessão."""
        return self.store.visitors(self.apartment(tool_context))

    def consultar_disponibilidade(self, area: str, data: str) -> dict:
        """Consulta área/data AAAA-MM-DD. Retorna apenas livre/ocupada, sem titular."""
        result = self.store.validate_area_date(area, data)
        if "erro" in result:
            return result
        return {"area": area, "data": data, "livre": self.store.availability(area, data)}

    def reservar(self, area: str, data: str, tool_context: ToolContext) -> dict:
        """Reserva área pelo ID e data AAAA-MM-DD. Taxa positiva exige confirmação externa."""
        apartment = self.apartment(tool_context)
        result = self.store.validate_area_date(area, data)
        if "erro" in result:
            return result
        # Não consulta o titular nem devolve o código de reservas de terceiros.
        if result["taxa"] > 0:
            pending = self.gate(
                tool_context, "reservar", {"area": area, "data": data, "taxa": result["taxa"]}
            )
            if pending:
                return pending
        return self.store.reserve(apartment, area, data, self.operation(tool_context))

    def cancelar_reserva(self, area: str, data: str, tool_context: ToolContext) -> dict:
        """Cancela reserva própria por área/data, sem confirmação. Não acessa outros apartamentos."""
        result = self.store.validate_area_date(area, data)
        if "erro" in result:
            return result
        return self.store.cancel(self.apartment(tool_context), area, data)

    def autorizar_visitante(self, nome: str, data: str, tool_context: ToolContext) -> dict:
        """Autoriza visitante por nome e data AAAA-MM-DD, sempre com confirmação externa."""
        apartment = self.apartment(tool_context)
        try:
            if date.fromisoformat(data).isoformat() != data or not nome.strip():
                raise ValueError
        except ValueError:
            return {"erro": "Informe nome e data no formato AAAA-MM-DD."}
        details = {"nome": nome.strip(), "data": data}
        pending = self.gate(tool_context, "autorizar_visitante", details)
        if pending:
            return pending
        return self.store.authorize(apartment, details["nome"], data, self.operation(tool_context))

    def consultar_regulamento(self, pergunta: str) -> dict:
        """Busca no regulamento somente o capítulo relacionado à pergunta; não aceita pedidos de dump."""
        text = (self.store.data_dir / "regulamento.md").read_text()
        chapters = re.split(r"(?m)^## ", text)[1:]
        stop = {
            "para",
            "como",
            "quais",
            "qual",
            "sobre",
            "pelo",
            "pela",
            "funciona",
            "ate",
            "horas",
            "quando",
            "posso",
            "regulamento",
            "condominio",
        }
        words = {w for w in re.findall(r"\w+", normalize(pergunta)) if len(w) > 3 and w not in stop}

        def score(chapter):
            title = normalize(chapter.split("\n", 1)[0])
            body = normalize(chapter)
            return sum((12 if w in title else 0) + min(body.count(w), 3) for w in words)

        best = max(chapters, key=score)
        if not words or score(best) == 0:
            return {
                "status": "especifique_assunto",
                "mensagem": "Informe o assunto específico da dúvida.",
            }
        # Somente artigos mais relevantes do capítulo escolhido chegam ao histórico.
        title, body = best.split("\n", 1)
        articles = re.split(r"(?=\*\*Art\. )", body)
        ranked = sorted(
            enumerate(articles),
            key=lambda a: sum(normalize(a[1]).count(w) for w in words),
            reverse=True,
        )
        selected = sorted(i for i, a in ranked[:2] if any(w in normalize(a) for w in words))
        return {
            "capitulo": title,
            "trechos": [articles[i].strip() for i in selected],
            "fonte": "dados/regulamento.md",
        }
