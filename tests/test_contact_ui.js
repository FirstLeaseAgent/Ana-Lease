const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function element(){return {textContent:'',value:'',hidden:false,disabled:false,children:[],handlers:{},type:'text',append(...items){this.children.push(...items);},replaceChildren(){this.children=[];},setAttribute(){},focus(){},scrollIntoView(){},addEventListener(n,fn){this.handlers[n]=fn;},showModal(){this.open=true;},close(){this.open=false;this.handlers.close?.();}};}
const nodes={};for(const id of ['conversation','composer','answer','send-answer','prompt','notice','documents','customer-workspace','welcome','start-application','contact-dialog','contact-form','contact-name','contact-phone','contact-status','send-contact','cancel-contact','exit-session','exit-screen','exit-title','capture-progress'])nodes['#'+id]=element();nodes['#customer-workspace'].hidden=true;nodes['#composer'].querySelector=()=>element();const help=element();let fails=true,n=0;const calls=[];
const ctx=vm.createContext({document:{activeElement:nodes['#answer'],querySelector:s=>nodes[s],querySelectorAll:()=>[help],createElement:element},crypto:{randomUUID:()=>String(++n)},fetch:async(path,opts)=>{calls.push({path,body:JSON.parse(opts.body)});if(path==='/contact-requests')return {ok:!fails,json:async()=>fails?{detail:'Intenta de nuevo'}:{ok:true}};return {ok:true,json:async()=>({reply:'Siguiente pregunta',stage:'capture'})};}});
vm.runInContext(fs.readFileSync('static/app.js','utf8'),ctx);
const run=s=>vm.runInContext(s,ctx),event={preventDefault(){}};
(async()=>{
 let cancelled=false;
 nodes['#send-answer'].handlers.pointerdown({isPrimary:true,button:0,preventDefault(){cancelled=true;}});
 assert.equal(cancelled,true,'A tap keeps focus on the answer until click');
 assert.equal(calls.length,0,'Touching the button must not submit before click');
 cancelled=false;
 nodes['#send-answer'].handlers.pointerdown({isPrimary:true,button:2,preventDefault(){cancelled=true;}});
 assert.equal(cancelled,false,'Secondary gestures keep their native behavior');
 assert.equal(nodes['#prompt'].textContent,'Tu respuesta','The question is not repeated beside the answer');

 for(const text of ['Quiero que me contacten','Quisiera hablar con un asesor','¿Pueden llamarme?','Contáctenme','Necesito asesoría'])assert.equal(run('contactIntent('+JSON.stringify(text)+')'),true,text);
 for(const text of ['Es el mismo contacto','Mi teléfono es 5512345678','No quiero que me contacten','¿Qué es un aval?'])assert.equal(run('contactIntent('+JSON.stringify(text)+')'),false,text);
 nodes['#start-application'].handlers.click();assert.equal(nodes['#customer-workspace'].hidden,false);assert.equal(nodes['#welcome'].hidden,true);
 run("state.step='conversation';state.intake='my-intake';state.currentQuestion='¿Cuál es tu cargo?';");nodes['#answer'].value='Director';help.handlers.click();assert.equal(nodes['#contact-dialog'].open,true);nodes['#cancel-contact'].handlers.click();assert.equal(nodes['#answer'].value,'Director');assert.equal(run('state.currentQuestion'),'¿Cuál es tu cargo?');
 nodes['#answer'].value='quiero que me contacten';await nodes['#composer'].handlers.submit(event);assert.equal(nodes['#contact-dialog'].open,true);assert.equal(run('state.step'),'conversation');assert.equal(calls.length,0);
 nodes['#contact-name'].value='Persona de prueba';nodes['#contact-phone'].value='55 1234 5678';await nodes['#contact-form'].handlers.submit(event);const id=calls[0].body.request_id;assert.equal(nodes['#send-contact'].hidden,false);assert.equal(nodes['#contact-status'].textContent,'Intenta de nuevo');fails=false;await nodes['#contact-form'].handlers.submit(event);assert.equal(calls[1].body.request_id,id);assert.equal(calls[1].body.intake_id,'my-intake');assert.equal(nodes['#send-contact'].hidden,true);assert.match(nodes['#contact-status'].textContent,/quedó registrada/);nodes['#cancel-contact'].handlers.click();assert.equal(run('state.step'),'conversation');
 nodes['#answer'].value='Director';await nodes['#composer'].handlers.submit(event);assert.equal(calls[2].path,'/intakes/my-intake/conversation');assert.equal(calls[2].body.question,'¿Cuál es tu cargo?');
 run('captureBusy=true');await nodes['#exit-session'].handlers.click();assert.equal(calls.length,3);assert.equal(run('state.intake'),'my-intake');run('captureBusy=false');
 nodes['#answer'].value='salir';await nodes['#composer'].handlers.submit(event);assert.equal(calls[3].path,'/auth/logout');assert.equal(nodes['#customer-workspace'].hidden,true);assert.equal(nodes['#exit-screen'].hidden,false);assert.equal(run('state.intake'),null);assert.equal(nodes['#conversation'].children.length,0);assert.equal(nodes['#answer'].value,'');
 for(const phrase of ['Salir','quiero salir','cerrar sesión','continuar después'])assert.equal(run('exitIntent('+JSON.stringify(phrase)+')'),true);
 for(const phrase of ['No quiero salir','Director','finalizar solicitud'])assert.equal(run('exitIntent('+JSON.stringify(phrase)+')'),false);
 console.log('Contact, retry, resumption, exit commands and session cleanup pass');
})().catch(e=>{console.error(e);process.exitCode=1;});
