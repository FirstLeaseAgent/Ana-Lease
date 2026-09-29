"""Build the isolated n8n conversational pilot. Credentials are selected after import."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT.joinpath('agent/system-prompt.txt').read_text()
SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['reply', 'actions'],
    'properties': {
        'reply': {'type': 'string'},
        'actions': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['type', 'target_id', 'source_id', 'role', 'field', 'value', 'evidence'],
            'properties': {
                'type': {'type': 'string', 'enum': ['add_participant','save_field','reuse_field','focus_participant']},
                'target_id': {'type': 'string'}, 'source_id': {'type': ['string','null']},
                'role': {'type': ['string','null'], 'enum': ['aval','representante',None]},
                'field': {'type': ['string','null'], 'enum': ['rfc','nombre','razon_social','nombre_comercial','actividad','pagina_web','correo_contacto','telefono','ocupacion','cargo',None]},
                'value': {'type': ['string','null']}, 'evidence': {'type': 'string'},
            },
        }},
    },
}

def node(name, kind, version, x, y, parameters, **extra):
    return {'id': name.lower().replace(' ','-'), 'name':name, 'type':'n8n-nodes-base.'+kind,
            'typeVersion':version, 'position':[x,y], 'parameters':parameters, **extra}

prepare = 'const schema = ' + json.dumps(SCHEMA, ensure_ascii=False) + ';\n'
prepare += """
const incoming = $input.first().json;
const body = incoming.body;
if (!body || body.version !== 1 || typeof body.message !== 'string' ||
    !body.message.trim() || body.message.length > 2000 || body.context?.version !== 1 ||
    !Array.isArray(body.context?.participants) || body.context.participants.length > 12 ||
    !Array.isArray(body.history) || body.history.length > 4 ||
    typeof incoming.instructions !== 'string' || !incoming.instructions.trim()) {
  return [{json:{ok:false,version:1,error:'invalid_request'}}];
}
return [{json:{ok:true,request:{
  model: incoming.model || 'gpt-5.4-mini',
  store:false, max_output_tokens:3000,
  instructions:incoming.instructions,
  input:[{role:'user',content:[{type:'input_text',text:JSON.stringify({message:body.message,context:body.context,history:body.history})}]}],
  text:{format:{type:'json_schema',name:'analease_capture',strict:true,schema}}
}}}];
"""

parse = """
const input = $input.first().json;
const fail = () => [{json:{ok:false,version:1,error:'ai_unavailable'}}];
if (input.error || (input.statusCode && input.statusCode !== 200)) return fail();
const data = input.body || input;
if (data.status !== 'completed' || !Array.isArray(data.output)) return fail();
const content = data.output.filter(x=>x.type==='message').flatMap(x=>x.content || []);
if (content.some(x=>x.type==='refusal')) return fail();
try {
  const raw = content.filter(x=>x.type==='output_text').map(x=>x.text).join('');
  const proposal = JSON.parse(raw);
  if (!proposal || Object.keys(proposal).sort().join(',') !== 'actions,reply' ||
      typeof proposal.reply !== 'string' || !proposal.reply || proposal.reply.length > 1800 ||
      !Array.isArray(proposal.actions) || proposal.actions.length > 16) return fail();
  return [{json:{ok:true,version:1,proposal}}];
} catch { return fail(); }
"""

nodes = [
    node('Entrada conversación','webhook',2.1,200,300,{
        'httpMethod':'POST','path':'analease-conversation','authentication':'headerAuth','responseMode':'responseNode','options':{}}),
    node('Instrucciones del agente','set',3.4,440,300,{
        'assignments':{'assignments':[
            {'id':'instructions','name':'instructions','value':PROMPT,'type':'string'},
            {'id':'model','name':'model','value':'gpt-5.4-mini','type':'string'}]},
        'includeOtherFields':True,'options':{}}),
    node('Preparar contexto y prompt','code',2,680,300,{'mode':'runOnceForAllItems','jsCode':prepare}),
    node('Entrada válida','if',2.2,920,300,{
        'conditions':{'options':{'caseSensitive':True,'leftValue':'','typeValidation':'strict','version':2},
          'combinator':'and','conditions':[{'id':'valid-input','leftValue':'={{ $json.ok }}','rightValue':True,'operator':{'type':'boolean','operation':'true'}}]},'options':{}}),
    node('Interpretar mensaje con OpenAI','httpRequest',4.2,1160,210,{
        'method':'POST','url':'https://api.openai.com/v1/responses',
        'authentication':'predefinedCredentialType','nodeCredentialType':'openAiApi',
        'sendBody':True,'specifyBody':'json','jsonBody':'={{ JSON.stringify($json.request) }}',
        'options':{'timeout':40000}},onError='continueRegularOutput',alwaysOutputData=True),
    node('Validar propuesta de IA','code',2,1400,210,{'mode':'runOnceForAllItems','jsCode':parse}),
    node('Responder conversación','respondToWebhook',1.5,1640,300,{
        'respondWith':'json','responseBody':'={{ JSON.stringify($json) }}',
        'options':{'responseCode':'={{ $json.ok ? 200 : ($json.error === "invalid_request" ? 400 : 502) }}'}}),
]
connections = {}
def link(source,target,output=0):
    entry=connections.setdefault(source,{'main':[]})['main']
    while len(entry)<=output: entry.append([])
    entry[output].append({'node':target,'type':'main','index':0})
link('Entrada conversación','Instrucciones del agente')
link('Instrucciones del agente','Preparar contexto y prompt')
link('Preparar contexto y prompt','Entrada válida')
link('Entrada válida','Interpretar mensaje con OpenAI',0)
link('Entrada válida','Responder conversación',1)
link('Interpretar mensaje con OpenAI','Validar propuesta de IA')
link('Validar propuesta de IA','Responder conversación')
workflow = {'name':'AnaLease - Conversación IA (piloto)','nodes':nodes,'connections':connections,
    'settings':{'executionOrder':'v1','saveDataSuccessExecution':'none','saveDataErrorExecution':'none',
                'saveManualExecutions':False,'saveExecutionProgress':False},'active':False}
path=Path(__file__).with_name('AnaLease-conversacion-IA.json')
path.write_text(json.dumps(workflow,ensure_ascii=False,indent=2)+'\n')
print(path)
