"""Generate the importable n8n workflow for AnaLease Syntage status checks."""
import json
from pathlib import Path


def node(name, kind, version, x, y, parameters, **extra):
    return {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type": "n8n-nodes-base." + kind,
        "typeVersion": version,
        "position": [x, y],
        "parameters": parameters,
        **extra,
    }


def code(name, x, y, js):
    return node(name, "code", 2, x, y, {
        "mode": "runOnceForAllItems",
        "jsCode": js.strip(),
    })


def http(name, x, y, url, query=None):
    parameters = {
        "method": "GET",
        "url": url,
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "X-API-Key", "value": "={{ $vars.API_SYNTAGE_KEY }}"},
        ]},
        "options": {"timeout": 20000},
    }
    if query:
        parameters["sendQuery"] = True
        parameters["queryParameters"] = {"parameters": query}
    return node(name, "httpRequest", 4.2, x, y, parameters,
                onError="continueRegularOutput")


def respond(name, x, y, status):
    return node(name, "respondToWebhook", 1.5, x, y, {
        "respondWith": "json",
        "responseBody": "={{ JSON.stringify($json) }}",
        "options": {"responseCode": status},
    })


nodes = [
    node("Entrada RFC", "webhook", 2.1, 200, 300, {
        "httpMethod": "POST",
        "path": "analease-syntage-status",
        "authentication": "headerAuth",
        "responseMode": "responseNode",
        "options": {},
    }),
    code("Validar RFC", 420, 300, """
const raw = $input.first().json.body?.rfc;
const rfc = typeof raw === 'string' ? raw.trim().toUpperCase() : '';
const valid = /^(?:[A-ZÑ&]{3}[0-9]{6}[A-Z0-9]{3}|[A-ZÑ&]{4}[0-9]{6}[A-Z0-9]{3})$/.test(rfc);
return [{json: {valid, rfc: valid ? rfc : undefined, person_type: valid ? (rfc.length === 12 ? 'legal' : 'physical') : undefined}}];
    """),
    node("RFC valido", "if", 2.2, 640, 300, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "strict", "version": 2},
                       "combinator": "and", "conditions": [{
                           "id": "valid-rfc", "leftValue": "={{ $json.valid }}",
                           "rightValue": True, "operator": {"type": "boolean", "operation": "true"},
                       }]},
        "options": {},
    }),
    http("Consultar entidades", 870, 220, "https://api.syntage.com/entities", [
        {"name": "taxpayer.id", "value": "={{ $('Validar RFC').first().json.rfc }}"},
        {"name": "itemsPerPage", "value": "100"},
    ]),
    http("Consultar credenciales SAT", 1100, 220, "https://api.syntage.com/credentials", [
        {"name": "rfc", "value": "={{ $('Validar RFC').first().json.rfc }}"},
        {"name": "itemsPerPage", "value": "100"},
    ]),
    code("Evaluar SAT", 1330, 220, """
const input = $('Validar RFC').first().json;
const e = $('Consultar entidades').first().json;
const c = $input.first().json;
const collection = x => x && Array.isArray(x['hydra:member']) && !x.error && !x.message;
if (!collection(e) || !collection(c)) {
  return [{json: {ok:false, error:'syntage_unavailable'}}];
}
const exact = e['hydra:member'].filter(x => String(x?.taxpayer?.id || '').toUpperCase() === input.rfc);
const credential = c['hydra:member'].filter(x => String(x?.rfc || '').toUpperCase() === input.rfc);
const partialEntityPage = !!e['hydra:view']?.['hydra:next'];
const partialCredentialPage = !!c['hydra:view']?.['hydra:next'];
if (exact.length > 1 || (exact.length === 0 && partialEntityPage) ||
    (!credential.some(x => x.status === 'valid') && partialCredentialPage)) {
  return [{json: {ok:false, error:'review_required'}}];
}
const statuses = credential.map(x => x.status);
const sat = statuses.includes('valid') ? 'valid' :
  (statuses.includes('pending') || statuses.includes('waiting')) ? 'pending' :
  statuses.length ? 'invalid' : 'missing';
const id = exact[0]?.id;
if (id && !/^[0-9a-f-]{36}$/i.test(String(id))) {
  return [{json: {ok:false, error:'review_required'}}];
}
return [{json: {
  ok:true, rfc:input.rfc, person_type:input.person_type,
  registered:!!id, entity_id:id || null, sat,
  // No personal data, credentials, or extracted documents enter the response.
}}];
    """),
    node("Entidad encontrada", "if", 2.2, 1550, 220, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "strict", "version": 2},
                       "combinator": "and", "conditions": [{
                           "id": "found-entity", "leftValue": "={{ $json.registered }}",
                           "rightValue": True, "operator": {"type": "boolean", "operation": "true"},
                       }]},
        "options": {},
    }),
    http("Consultar autorizaciones Buro", 1780, 130,
         "={{ 'https://api.syntage.com/entities/' + $('Evaluar SAT').first().json.entity_id + '/datasources/mx/buro-de-credito/authorizations' }}",
         [{"name": "itemsPerPage", "value": "1000"}]),
    code("Resultado con Buro", 2010, 130, """
const base = $('Evaluar SAT').first().json;
if (!base.ok) return [{json:{ok:false,error:base.error}}];
const data = $input.first().json;
if (!Array.isArray(data?.['hydra:member']) || data.error || data.message) {
  return [{json:{ok:false,error:'syntage_unavailable'}}];
}
const now = Date.now();
const authorized = data['hydra:member'].some(a =>
  String(a?.rfc || '').toUpperCase() === base.rfc &&
  a.isValid === true && !a.deletedAt &&
  Number.isFinite(Date.parse(a.authorizedUntil)) && Date.parse(a.authorizedUntil) > now
);
if (!authorized && data['hydra:view']?.['hydra:next']) {
  return [{json:{ok:false,error:'review_required'}}];
}
const buro = authorized ? 'valid' : 'missing';
const action = base.sat === 'pending' ? 'wait' :
  base.sat === 'valid' && buro === 'valid' ? 'continue' : 'onboarding';
return [{json:{ok:true,person_type:base.person_type,registered:true,
               sat:base.sat,buro,next_action:action}}];
    """),
    code("Resultado sin entidad", 1780, 350, """
const base = $input.first().json;
if (!base.ok) return [{json:{ok:false,error:base.error}}];
// A standalone credential without a matching entity needs internal review.
if (base.sat !== 'missing') return [{json:{ok:false,error:'review_required'}}];
return [{json:{ok:true,person_type:base.person_type,registered:false,
               sat:'missing',buro:'missing',next_action:'onboarding'}}];
    """),
    code("RFC incorrecto", 870, 460, """
return [{json:{ok:false,error:'invalid_rfc'}}];
    """),
    respond("Responder estado", 2240, 130, "={{ $json.ok ? 200 : 502 }}"),
    respond("Responder sin entidad", 2010, 350, "={{ $json.ok ? 200 : 502 }}"),
    respond("Responder RFC incorrecto", 1100, 460, 422),
]

connections = {}
def link(source, target, output=0):
    entry = connections.setdefault(source, {"main": []})["main"]
    while len(entry) <= output:
        entry.append([])
    entry[output].append({"node": target, "type": "main", "index": 0})


link("Entrada RFC", "Validar RFC")
link("Validar RFC", "RFC valido")
link("RFC valido", "Consultar entidades", 0)
link("RFC valido", "RFC incorrecto", 1)
link("Consultar entidades", "Consultar credenciales SAT")
link("Consultar credenciales SAT", "Evaluar SAT")
link("Evaluar SAT", "Entidad encontrada")
link("Entidad encontrada", "Consultar autorizaciones Buro", 0)
link("Entidad encontrada", "Resultado sin entidad", 1)
link("Consultar autorizaciones Buro", "Resultado con Buro")
link("Resultado con Buro", "Responder estado")
link("Resultado sin entidad", "Responder sin entidad")
link("RFC incorrecto", "Responder RFC incorrecto")

workflow = {
    "name": "AnaLease - Estado Syntage por RFC",
    "nodes": nodes,
    "connections": connections,
    "settings": {
        "executionOrder": "v1",
        "saveDataSuccessExecution": "none",
        "saveDataErrorExecution": "none",
        "saveManualExecutions": False,
    },
    "active": False,
}

path = Path(__file__).with_name("AnaLease - estado Syntage por RFC.json")
path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n")
print(path)
