const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const wf = JSON.parse(fs.readFileSync('n8n/AnaLease-conversacion-IA.json','utf8'));
const fixture = JSON.parse(fs.readFileSync('agent/test-context.json','utf8'));
const settings = wf.nodes.find(n=>n.name==='Instrucciones del agente').parameters.assignments.assignments;
const instructions=settings.find(s=>s.name==='instructions').value;
function run(name,json){return vm.runInNewContext('(function(){'+wf.nodes.find(n=>n.name===name).parameters.jsCode+'\n})()',{$input:{first:()=>({json})}})[0].json;}
const prepared=run('Preparar contexto y prompt',{body:fixture,instructions,model:'gpt-5.4-mini'});
assert.equal(prepared.ok,true);
assert.equal(prepared.request.store,false);
assert.equal(prepared.request.text.format.strict,true);
assert.equal(run('Preparar contexto y prompt',{body:{},instructions}).ok,false);
const proposal={reply:'¿Cuál es el RFC del representante?',actions:[]};
const valid=run('Validar propuesta de IA',{status:'completed',output:[{type:'message',content:[{type:'output_text',text:JSON.stringify(proposal)}]}]});
assert.equal(valid.ok,true);
assert.equal(valid.proposal.reply,proposal.reply);
for(const response of [{error:{message:'private error'}},{status:'incomplete',output:[]},{status:'completed',output:[{type:'message',content:[{type:'refusal',refusal:'private'}]}]}]){
  const out=run('Validar propuesta de IA',response);assert.equal(out.ok,false);assert.equal(out.error,'ai_unavailable');assert.equal(JSON.stringify(out).includes('private'),false);
}
assert.equal(wf.nodes.find(n=>n.name==='Interpretar mensaje con OpenAI').parameters.nodeCredentialType,'openAiApi');
assert.equal(wf.nodes.some(n=>n.credentials),false);

// Exercise the real frontend: an authorization notice must hold the next question until OK.
const element=()=>({textContent:'',value:'',append(){},scrollIntoView(){},focus(){}});
const elements={'#conversation':element(),'#composer':element(),'#answer':element(),'#prompt':element(),'#notice':element()};
let submit;let calls=0;
let undo;
elements['#undo-answer']={disabled:false,addEventListener:(name,fn)=>{undo=fn;}};
elements['#composer'].querySelector=()=>({disabled:false});
elements['#composer'].addEventListener=(name,fn)=>{submit=fn;};
const browser=vm.createContext({document:{querySelector:s=>elements[s],createElement:element},crypto:{randomUUID:()=> '00000000-0000-4000-8000-000000000003'},fetch:async()=>{calls++;return {ok:true,json:async()=>({reply:'¿Cuál es su cargo?',active_id:'rep',authorization_links:[{url:'https://registro.syntage.com/example',context:'Aval'}]})};}});
vm.runInContext(fs.readFileSync('static/app.js','utf8'),browser);
vm.runInContext("state.step='conversation';state.intake='intake';",browser);
(async()=>{
  elements['#answer'].value='Mi aval es el mismo contacto';await submit({preventDefault(){}});
  assert.equal(vm.runInContext('state.step',browser),'conversation_ack');
  assert.equal(calls,1);
  elements['#answer'].value='sí';await submit({preventDefault(){}});
  assert.equal(vm.runInContext('state.step',browser),'conversation_ack');
  elements['#answer'].value='OK';await submit({preventDefault(){}});
  assert.equal(vm.runInContext('state.step',browser),'conversation');
  assert.equal(calls,1);
  const undoEvent={currentTarget:elements['#undo-answer']};
  const undoPending=undo(undoEvent);
  assert.equal(elements['#undo-answer'].disabled,true);
  undoEvent.currentTarget=null; // DOM events clear currentTarget after dispatch.
  await undoPending;
  assert.equal(elements['#undo-answer'].disabled,false);
  assert.equal(calls,2);
  console.log('n8n request, parser, credentials and frontend acknowledgement tests passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
