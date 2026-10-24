# Assistente do Residencial Aurora

## Arquitetura

API Python 3.12+ com FastAPI, Google ADK **2.9.1**, Gemini e SQLite. O contrato HTTP
fica disponível em `http://localhost:8000/docs` e `/openapi.json` quando a API está
em execução. A aplicação não depende de banco externo.

Os agentes são definidos em [`aurora/agents.py`](aurora/agents.py):

| Agente | Responsabilidade | Acionamento e motivo |
|---|---|---|
| `aurora` | Entender o pedido e encaminhá-lo | Entrada da conversa; reúne os especialistas em `sub_agents` |
| `reservas` | Disponibilidade, reservas e cancelamentos | Transferência pelo agente principal ou por outro especialista; concentra as tools de reservas |
| `visitantes` | Consulta e autorização de visitantes próprios | Transferência; separa ações que liberam acesso |
| `regulamento` | Responder perguntas usando trechos do regulamento | Transferência; só recebe os trechos recuperados pela tool |

Todos usam o modelo configurado em `GEMINI_MODEL`, com padrão
`gemini-3.1-flash-lite`. As transferências permitem voltar ao principal e acessar
outros especialistas em mensagens seguintes. Nenhum agente tem tools para mudar a
identidade da sessão ou registrar uma aprovação por texto.

[`aurora/runtime.py`](aurora/runtime.py) integra `App`, `Runner` e
`DatabaseSessionService`. A retomada está habilitada por
`ResumabilityConfig(is_resumable=True)`. A API devolve as solicitações
`adk_request_confirmation`; a resposta HTTP é convertida em `FunctionResponse`
com o ID da confirmação e o `invocation_id` original, permitindo ao Runner
retomar o agente e a tool corretos. Cada execução tem limite de oito chamadas ao
modelo para conter ciclos de transferência. A confirmação de tools é experimental no ADK;
a versão é fixada e a integração com sessão persistida é coberta por testes.

[`aurora/tools.py`](aurora/tools.py) acessa o domínio por meio de
[`aurora/storage.py`](aurora/storage.py). Há dois bancos locais em `.state/`:
`condominio.db` para dados, vínculos e confirmações; `sessoes.db` para o histórico
e estado do ADK. Os JSONs e o Markdown de `dados/` são somente leitura.

| Método | Rota | Resultado |
|---|---|---|
| POST | `/sessoes` | `201`, recebe `apartamento`, devolve `session_id` |
| POST | `/sessoes/{session_id}/mensagens` | `200`, recebe `texto`, devolve resposta e pendências |
| POST | `/sessoes/{session_id}/confirmacoes` | `200`, recebe `id` e `confirmado`; `409` se não estiver pendente nessa sessão |
| GET | `/sessoes/{session_id}/eventos` | `200`, todos os eventos completos, em ordem |
| GET | `/apartamentos/{apartamento}/reservas` | `200`, reservas ativas, sem passar pelo modelo |
| GET | `/apartamentos/{apartamento}/visitantes` | `200`, visitantes, sem passar pelo modelo |

As três rotas que recebem `session_id` devolvem `404` para sessão inexistente.
Enquanto há pendência, mensagens novas apenas devolvem as pendências e pedem a
resposta pela rota de confirmações. Apartamento inexistente na criação recebe
`422`; nas rotas de verificação, devolve lista vazia. Falhas da API Gemini geram
`503` com mensagem genérica. As rotas de verificação são administrativas no
contexto do desafio e não têm autenticação, conforme o enunciado.

## Garantias

1. **Cobrança e acesso exigem confirmação externa.**
   Em [`Tools.gate`](aurora/tools.py), `reservar` consulta a taxa no banco e
   `autorizar_visitante` sempre passa pela confirmação. Sem aprovação, a tool
   apenas chama `request_confirmation`. A autorização efetiva exige tanto o
   `tool_confirmation` do ADK quanto o contexto privado `approval`, vinculado à
   sessão, à chamada original, à ação e aos detalhes. Só `Runtime.confirm`
   estabelece esse contexto. `Store.claim_confirmation`, em
   [`aurora/storage.py`](aurora/storage.py), usa transação para consumir uma
   pendência pertencente à sessão. IDs desconhecidos, de outra sessão ou já
   respondidos recebem `409`. Negar não grava reserva nem visitante. Área sem
   taxa e cancelamento próprio dispensam confirmação. A tabela `efeitos` torna
   as gravações idempotentes pela chave sessão/chamada. `Runtime.run` também
   verifica se a tool confirmada realmente foi retomada, evitando sucesso
   silencioso sem execução.

2. **Cada sessão pertence a um único apartamento.**
   `Runtime.create_session` grava o vínculo uma vez em `vinculos`.
   `Tools.apartment` lê esse vínculo pelo ID da sessão; não confia em identidade
   escrita na conversa nem em um estado mutável escolhido pelo modelo. As tools
   não recebem parâmetro de apartamento. `Store.reservations`, `visitors` e
   `cancel` filtram pelo apartamento autenticado. Disponibilidade só retorna
   livre/ocupada; a tentativa de reserva ocupada não devolve titular nem código.
   O banco de verificação mantém os dados completos fora da conversa.

3. **Conversas e dados sobrevivem ao reinício.**
   `Runtime.__init__` abre `DatabaseSessionService` em `sessoes.db` e `Store`
   abre `condominio.db`. A inicialização semeia dados apenas quando ainda não
   existem; subir a API nunca restaura o estado automaticamente. Confirmações
   pendentes e o ID de sua execução também são persistidos. `Runtime.events`
   devolve o conteúdo completo dos eventos salvos, incluindo chamadas de tools.
   Cancelamentos mantêm a linha inativa; o código permanece como chave primária
   e nunca pode ser usado por outra reserva.

4. **O regulamento é consultado por assunto.**
   `Tools.consultar_regulamento` faz busca lexical local nos capítulos e
   artigos de `dados/regulamento.md`. Apenas o título do capítulo mais relevante
   e seus artigos selecionados são retornados ao agente. Não há fallback que
   carregue o documento inteiro. Perguntas sem assunto identificável pedem
   esclarecimento. O principal não recebe o regulamento nas instruções; o texto
   completo não é gravado em eventos.

5. **Exclusividade no instante da gravação.**
   Em `Store.__init__`, o índice único parcial `reserva_exclusiva` proíbe duas
   linhas ativas com o mesmo par `(area, data)`. `Store.reserve` grava em
   transação `BEGIN IMMEDIATE` e converte o conflito em resultado normal
   `indisponivel`. A proteção vale entre conexões e processos, sem depender de
   uma consulta prévia. Duas aprovações de sessões diferentes podem receber
   `200`, com apenas uma reserva criada. O lock de sessão em `Runtime` organiza
   mensagens da mesma sessão; a exclusividade entre moradores está no banco.

## Como rodar

Pré-requisitos: Python 3.12 ou 3.13, [uv](https://docs.astral.sh/uv/) e uma chave
da [API Gemini](https://aistudio.google.com/api-keys) com cota disponível para um
modelo que suporte chamadas de função. Confira os modelos e limites ativos no
AI Studio; eles variam por projeto.

Na raiz do repositório:

```sh
cp .env.example .env
uv sync --locked
```

Preencha `.env` localmente:

| Variável | Uso |
|---|---|
| `GOOGLE_API_KEY` | Chave do Google AI Studio, obrigatória para conversas reais |
| `GOOGLE_GENAI_USE_VERTEXAI` | `FALSE` para usar a API com chave do AI Studio; pode ficar vazia |
| `GEMINI_MODEL` | Modelo de todos os agentes; vazio usa `gemini-3.1-flash-lite` |
| `AURORA_STATE_DIR` | Diretório de persistência; vazio usa `.state/` na raiz |

O `.env` é ignorado pelo Git. Não coloque credenciais em código, comandos
versionados ou arquivos de teste. O `.env.example` contém apenas nomes, sem
valores.

Com a API parada, restaure os dados iniciais:

```sh
uv run aurora reset
```

Esse comando restaura reservas e visitantes, apaga sessões, eventos e
confirmações locais e preserva `dados/`. Para iniciar:

```sh
uv run aurora serve
```

A API responde em `http://localhost:8000`. Pare com `Ctrl+C` e use o mesmo comando
para reiniciar, sem `reset`, para conservar o estado.

Exemplo de conversa:

```sh
curl -X POST http://localhost:8000/sessoes \
  -H 'Content-Type: application/json' -d '{"apartamento":"101"}'
```

Use o `session_id` retornado em:

```sh
curl -X POST http://localhost:8000/sessoes/SESSION_ID/mensagens \
  -H 'Content-Type: application/json' \
  -d '{"texto":"Reserve o salão de festas para 2030-04-20."}'
```

Aprovação pela rota própria, usando o ID de `confirmacoes_pendentes`:

```sh
curl -X POST http://localhost:8000/sessoes/SESSION_ID/confirmacoes \
  -H 'Content-Type: application/json' \
  -d '{"id":"CONFIRMACAO_ID","confirmado":true}'
```

Como alternativa ao Python local, com Docker Compose e o `.env` preenchido:

```sh
docker compose build
docker compose run --rm api uv run --no-sync aurora reset
docker compose up api
```

O estado fica em `.state/` no host e persiste após parar o container. Use apenas
uma das formas de execução por vez na porta 8000.

Os testes de integração usam o SDK Gemini e o ADK reais contra um simulador HTTP
local do Postman. O mock implementa o formato de `generateContent`, transferências
e chamadas de função com cenários determinísticos `SIM:`. Isso verifica contrato,
retomada, autorização, isolamento, persistência e concorrência; não mede a
interpretação de linguagem natural pelo modelo real. O mock foi escrito a partir
do [contrato oficial Gemini](https://ai.google.dev/api/generate-content).

Com Postman CLI 1.70+ instalado, inicie o simulador em um terminal:

```sh
postman simulation run postman/simulations/gemini.sim.yaml --port 4900 --no-history
```

Em outro terminal:

```sh
uv run pytest -q
uv run ruff check aurora tests scripts
```

Os testes substituem a chave por um valor fictício e usam bancos temporários.
`tests/test_process_restart.py` inicia e encerra processos reais da API para
verificar reinício, recuperação de eventos e aprovação de uma pendência antiga.

Para executar o roteiro de aceite com Gemini real e a chave do `.env`:

```sh
uv run python scripts/validate_live.py
```

Esse comando usa um diretório temporário, inicia a API em uma porta local livre,
executa o roteiro e reinicia o processo sem restaurar o estado. Consome chamadas
ao modelo e depende da cota do projeto. A execução informa falhas de aceite; não
altera o banco de uso normal em `.state/`.

Para guardar requisições, eventos e contagem de tokens sem repetir chamadas ao
modelo, use um diretório novo de evidências, ignorado pelo Git:

```sh
uv run python scripts/validate_live.py --artifacts .state/aceite-real
```

Validação executada em 24/10/2026: 11 testes locais passaram contra o simulador
HTTP. O roteiro de aceite também passou com Gemini `gemini-3.1-flash-lite`,
incluindo reinício do processo e duas aprovações simultâneas com uma única
reserva criada. Essa passagem registrou 39 respostas do modelo e 105.657 tokens
no total. As evidências locais ficam fora do Git.
