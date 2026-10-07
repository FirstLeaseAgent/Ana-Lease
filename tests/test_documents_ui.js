const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function element(){return {textContent:'',value:'',hidden:false,disabled:false,children:[],handlers:{},files:[],append(...items){this.children.push(...items);},replaceChildren(){this.children=[];},setAttribute(){},focus(){},scrollIntoView(){},click(){},querySelectorAll(){const visit=n=>[n,...n.children.flatMap(c=>typeof c==='object'?visit(c):[])];return visit(this).filter(n=>n.type==='button'||n.type==='file'||n.tag==='select');},addEventListener(name,fn){this.handlers[name]=fn;}};}
const nodes={'#conversation':element(),'#composer':element(),'#answer':element(),'#prompt':element(),'#notice':element(),'#documents':element()};
nodes['#composer'].querySelector=()=>element();
let uploadFails=true;let uploadAvailable=false;let requiredMissing=1;let submitted=false;
const calls=[];
const browser=vm.createContext({document:{querySelector:s=>nodes[s],createElement:element},crypto:{randomUUID:()=> '00000000-0000-4000-8000-000000000005'},Map,XMLHttpRequest:class{constructor(){this.upload={};}open(method,path){this.method=method;this.path=path;}setRequestHeader(){}send(file){calls.push({path:this.path,options:{method:this.method,body:file}});this.upload.onprogress({lengthComputable:true,loaded:1,total:2});if(uploadFails){this.onerror();return;}this.upload.onprogress({lengthComputable:true,loaded:2,total:2});this.status=200;this.responseText=JSON.stringify({ok:true,status:'received'});this.onload();}},fetch:async(path,options={})=>{
 calls.push({path,options});
 if(path.endsWith('/conversation'))return {ok:true,json:async()=>({reply:'¿Cuál es su RFC?',stage:'capture'})};
 if(path.endsWith('/finalize')){submitted=true;return {ok:true,json:async()=>({ok:true,status:'submitted'})};}
 if(options.method&&options.method!=='GET')return {ok:true,json:async()=>({ok:true,status:'received'})};
 return {ok:true,json:async()=>({message:'Continúa con documentos',status:submitted?'submitted':'open',required_missing:requiredMissing,upload_available:uploadAvailable,documents:[{participant_id:'company',participant:'Solicitante (PM)',code:'documento_empresa',label:'Documento ficticio',required:true,applicable:true,status:'pending',reuse_sources:[],members:[{participant_id:'company',participant:'Representante (PF) · N*****'},{participant_id:'aval',participant:'Aval (PF) · N*****'}]}]})};
}});
vm.runInContext(fs.readFileSync('static/app.js','utf8'),browser);
vm.runInContext(fs.readFileSync('static/documents.js','utf8'),browser);
vm.runInContext("state.intake='intake';state.step='documents';state.currentQuestion='Continuemos con documentos';",browser);
const flatten=node=>[node,...node.children.flatMap(child=>typeof child==='object'?flatten(child):[])];
(async()=>{
 await vm.runInContext('loadDocuments()',browser);
 let all=flatten(nodes['#documents']);
 assert.equal(all.filter(n=>n.type==='file').length,1);
 assert.ok(all.some(n=>n.textContent.includes('Un solo archivo cubre estos roles')&&n.textContent.includes('Representante (PF), Aval (PF)')));
 assert.equal(all.find(n=>n.textContent==='Subir archivo').disabled,true);assert.equal(all.find(n=>n.textContent==='Finalizar solicitud').disabled,false);
 const defer=all.find(n=>n.textContent==='No lo tengo ahora');await defer.handlers.click();
 assert.ok(calls.some(c=>c.path.endsWith('/documento_empresa/defer')&&c.options.method==='POST'));
 uploadAvailable=true;await vm.runInContext('loadDocuments()',browser);
 all=flatten(nodes['#documents']);const file=all.find(n=>n.type==='file');file.files=[{name:'prueba.pdf',size:9,type:'application/pdf'}];file.handlers.change();
 await all.find(n=>n.textContent==='Subir archivo').handlers.click();
 assert.ok(nodes['#notice'].textContent.includes('Reintenta'));uploadFails=false;await all.find(n=>n.textContent==='Subir archivo').handlers.click();
 const uploads=calls.filter(c=>c.path.includes('/documento_empresa/uploads/'));assert.equal(uploads[0].path,uploads[1].path);
 assert.ok(calls.some(c=>c.path.includes('/documento_empresa/uploads/')&&c.options.method==='PUT'&&c.options.body===file.files[0]));
 all=flatten(nodes['#documents']);const drop=all.find(n=>n.className==='drop-zone');drop.handlers.drop({preventDefault(){},dataTransfer:{files:[{name:'arrastrado.pdf',size:10,type:'application/pdf'}]}});assert.ok(all.some(n=>n.textContent.includes('arrastrado.pdf')));
 all=flatten(nodes['#documents']);await all.find(n=>n.textContent==='Agregar otro aval').handlers.click();
 assert.equal(vm.runInContext('state.step',browser),'conversation');assert.equal(nodes['#documents'].hidden,true);
 const capture=calls.find(c=>c.path.endsWith('/conversation'));assert.equal(JSON.parse(capture.options.body).message,'Quiero agregar un aval');
 requiredMissing=1;await vm.runInContext('loadDocuments()',browser);
 all=flatten(nodes['#documents']);const finish=all.find(n=>n.textContent==='Finalizar solicitud');assert.equal(finish.disabled,false);await finish.handlers.click();
 all=flatten(nodes['#documents']);assert.equal(vm.runInContext('state.step',browser),'submitted');assert.equal(all.some(n=>n.type==='file'||n.textContent==='Finalizar solicitud'||n.textContent==='Agregar otro aval'),false);
 assert.ok(calls.some(c=>c.path.endsWith('/finalize')&&c.options.method==='POST'));
 assert.ok(all.some(n=>n.textContent==='Pendiente'));
 console.log('Document UI gates uploads, saves deferrals, sends files and returns to participant capture');
})().catch(error=>{console.error(error);process.exitCode=1;});
