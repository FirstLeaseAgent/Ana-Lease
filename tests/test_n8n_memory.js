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
console.log('Updated n8n workflow transmits durable memory');
body.context.participants=[{id:'share-test',role:'accionista',subject_type:'PF',known_fields:['rfc','nombre'],missing_fields:['curp','porcentaje_participacion']}];
const withShareholder=vm.runInNewContext('(function(){'+prepare+'\n})()',{
  $input:{first:()=>({json:{body,instructions:'Instrucciones de prueba'}})},
})[0].json;
assert.equal(withShareholder.ok,true);
assert.equal(JSON.parse(withShareholder.request.input[0].content[0].text).context.participants[0].role,'accionista');
console.log('Updated n8n workflow accepts shareholder context');

const actionProperties=prepared.request.text.format.schema.properties.actions.items.properties;
assert.ok(actionProperties.role.enum.includes('accionista'));
assert.ok(actionProperties.type.enum.includes('finish_shareholders'));
assert.ok(actionProperties.type.enum.includes('finish_guarantors'));
assert.equal(actionProperties.field.enum,undefined);
assert.equal(prepared.request.model,'gpt-5.4-mini');
console.log('Schema enables shareholder creation, list completion, CURP, percentages and catalog fields');
