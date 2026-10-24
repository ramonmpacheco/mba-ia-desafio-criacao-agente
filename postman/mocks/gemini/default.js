/**
 * Subconjunto HTTP de models.generateContent, baseado em
 * https://ai.google.dev/api/generate-content e /function-calling.
 * Respostas determinísticas SIM:<json> para testar o runtime, não a linguagem do Gemini.
 * Este mock é local e não foi obtido de um workspace do provedor.
 */
const http = require('http');
const { randomUUID } = require('crypto');
http.createServer(async (req, res) => {
  const send = (code, body) => { res.writeHead(code, {'Content-Type': 'application/json'}); res.end(JSON.stringify(body)); };
  if (req.method !== 'POST' || !/^\/v1beta\/models\/[^/]+:generateContent$/.test(req.url))
    return send(501, {error: {code: 501, message: 'Endpoint not modeled', status: 'UNIMPLEMENTED'}});
  const body = req.body;
  if (!body || !Array.isArray(body.contents))
    return send(400, {error: {code: 400, message: 'contents required', status: 'INVALID_ARGUMENT'}});
  let scenario, scenarioIndex = -1;
  for (let i = 0; i < body.contents.length; i++) {
    for (const part of body.contents[i].parts || []) {
      if (part.text && part.text.startsWith('SIM:')) {
        scenario = JSON.parse(part.text.slice(4)); scenarioIndex = i;
      }
    }
  }
  if (!scenario) return send(400, {error: {code: 400, message: 'Explicit SIM scenario required', status: 'INVALID_ARGUMENT'}});
  const declarations = (body.tools || []).flatMap(t => t.functionDeclarations || []);
  const hasTool = name => declarations.some(d => d.name === name);
  const targetAgent = {
    reservar: 'reservas', cancelar_reserva: 'reservas', minhas_reservas: 'reservas', consultar_disponibilidade: 'reservas',
    autorizar_visitante: 'visitantes', meus_visitantes: 'visitantes', consultar_regulamento: 'regulamento'
  }[scenario.tool];
  const results = body.contents.slice(scenarioIndex + 1).flatMap(c => c.parts || [])
    .filter(p => p.functionResponse && p.functionResponse.name === scenario.tool);
  let part;
  if (results.length) {
    const result = results[results.length - 1].functionResponse.response;
    part = {text: JSON.stringify(result)};
  } else if (!hasTool(scenario.tool)) {
    part = {functionCall: {id: randomUUID(), name: 'transfer_to_agent', args: {agent_name: targetAgent}}};
  } else {
    part = {functionCall: {id: randomUUID(), name: scenario.tool, args: scenario.args || {}}};
  }
  return send(200, {candidates: [{content: {role:'model', parts:[part]}, finishReason:'STOP'}], modelVersion:'gemini-contract-mock', usageMetadata:{promptTokenCount:1,candidatesTokenCount:1,totalTokenCount:2}});
}).listen(process.env.PORT || 4901);
