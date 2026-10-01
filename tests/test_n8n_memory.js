const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const workflow = JSON.parse(fs.readFileSync('n8n/AnaLease-conversacion-IA.json','utf8'));
const prepare = workflow.nodes.find(n => n.name === 'Preparar contexto y prompt').parameters.jsCode;
const memory = {
  version:1,
  confirmed_relations:[{type:'same_person',target_id:'rep-test',source_id:'contact-test',source_role:'contacto'}],
  earlier_turns:Array.from({length:9},(_,i)=>({user:'Turno '+i,assistant:'Respuesta mostrada '+i,capture_targets:[]})),
};
const body = {version:1,message:'Director General',context:{version:1,participants:[],conversation_memory:memory},
              history:Array.from({length:4},()=>({user:'Dato ficticio',assistant:'Pregunta actual'}))};
const prepared = vm.runInNewContext('(function(){'+prepare+'\n})()',{
  $input:{first:()=>({json:{body,instructions:'Instrucciones de prueba',model:'gpt-5.4-mini'}})},
})[0].json;
assert.equal(prepared.ok,true);
const transmitted = JSON.parse(prepared.request.input[0].content[0].text);
assert.deepEqual(transmitted.context.conversation_memory,memory);
assert.equal(transmitted.history.length,4);
assert.equal(prepared.request.store,false);
assert.equal(prepared.request.text.format.strict,true);
console.log('Existing n8n workflow transmits durable memory without changing its contract');
