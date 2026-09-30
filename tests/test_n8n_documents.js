const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const workflow=JSON.parse(fs.readFileSync('n8n/AnaLease-documentos-SharePoint.json','utf8'));
function run(name,json){const code=workflow.nodes.find(n=>n.name===name).parameters.jsCode;return vm.runInNewContext('(function(){'+code+'\n})()',{$input:{first:()=>({json})},Buffer})[0];}
const body={version:1,intake_id:'00000000-0000-4000-8000-000000000001',participant_id:'00000000-0000-4000-8000-000000000002',upload_id:'00000000-0000-4000-8000-000000000003',document_code:'documento_empresa',mime_type:'application/pdf',content_base64:Buffer.from('%PDF-1.4\n').toString('base64')};
const valid=run('Preparar archivo',{body});assert.equal(valid.json.ok,true);assert.ok(valid.binary.data);assert.ok(valid.json.file_name.endsWith('.pdf'));assert.equal(valid.json.content_base64,undefined);
for(const bad of [{...body,document_code:'../../bad'},{...body,content_base64:'not base64!'},{...body,mime_type:'image/png'},{...body,intake_id:'other'}])assert.equal(run('Preparar archivo',{body:bad}).json.ok,false);
assert.equal(run('Confirmar guardado',{id:'storage-test-id'}).json.ok,true);
assert.equal(run('Confirmar guardado',{error:{message:'private error'}}).json.ok,false);
assert.equal(workflow.nodes.find(n=>n.name==='Guardar en SharePoint').typeVersion,1);
assert.equal(workflow.nodes.some(n=>n.credentials),false);
assert.equal(workflow.settings.saveDataSuccessExecution,'none');
assert.equal(workflow.nodes.find(n=>n.name==='Entrada documento').parameters.authentication,'headerAuth');
console.log('Document workflow validates file, scopes identifiers and confirms storage without credentials in export');
