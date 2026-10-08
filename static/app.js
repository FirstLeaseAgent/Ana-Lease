const convo = document.querySelector('#conversation');
const form = document.querySelector('#composer');
const field = document.querySelector('#answer');
const prompt = document.querySelector('#prompt');
const notice = document.querySelector('#notice');
const state = {step:'email', email:'', intake:null, pendingIntakeId:null, participant:null, questions:[], index:0, pendingTurn:null, pendingChatReply:null};
const labels = {razon_social:'¿Cuál es la razón social?', nombre:'¿Cuál es el nombre completo?', nombre_comercial:'¿Cuál es el nombre comercial?', pagina_web:'¿Cuál es su página web?', correo_contacto:'¿Cuál es el correo de contacto?', telefono:'¿Cuál es el teléfono?', actividad:'¿A qué se dedica?', ocupacion:'¿Cuál es su ocupación?',cargo:'¿Cuál es su cargo?'};
function bubble(value,mine=false){const el=document.createElement('div');el.className='bubble'+(mine?' mine':'');el.textContent=value;convo.append(el);const area=document.querySelector('#conversation-scroll');if(area)area.scrollTop=area.scrollHeight;else el.scrollIntoView({block:'nearest'});}
function authorization(url,id){if(!url)return false;state.pendingIntakeId=id;state.step='authorization_ack';const el=document.createElement('div');el.className='bubble';el.append('Para poder revisar tu información fiscal y de Buró de Crédito, es necesario completar las autorizaciones requeridas en Syntage: ');const link=document.createElement('a');link.href=url;link.textContent='Abrir autorización en Syntage';link.target='_blank';link.rel='noopener noreferrer';el.append(link);convo.append(el);ask('Puedes seguir capturando tus datos mientras realizas la autorización. Escribe OK para confirmar que viste este aviso y continuar. OK no sustituye la autorización en Syntage.');return true;}
function ask(value,type='text'){bubble(value);prompt.textContent='Tu respuesta';field.type=type;field.value='';if(!document.querySelector('#customer-workspace')?.hidden)field.focus();}
function status(value){notice.textContent=value;}
function setCaptureProgress(label,completed,total,stage){const wrap=document.querySelector('#capture-progress');if(!wrap)return;wrap.hidden=false;document.querySelector('#capture-progress-label').textContent=label;document.querySelector('#capture-progress-count').textContent=total?completed+' de '+total+' · '+Math.round(completed/total*100)+'%':'100%';document.querySelector('#capture-progress-bar').value=total?Math.round(completed/total*100):100;document.querySelector('#capture-stage-label').textContent=stage;}
function showConversation(out){if(out.progress)setCaptureProgress(out.progress.label,out.progress.completed,out.progress.total,'Solicitante → Representante → Accionistas → Avales → Documentos');state.pendingChatReply=null;state.currentQuestion=out.reply;if(out.stage==='documents'){state.step='documents';form.hidden=true;bubble(out.reply);loadDocuments().catch(error=>status(error.message));return;}state.step='conversation';form.hidden=false;const docs=document.querySelector('#documents');if(docs)docs.hidden=true;ask(out.reply);}
function conversationAuthorization(out){if(!out.authorization_links?.length)return false;state.pendingChatReply=out;state.step='conversation_ack';for(const notice of out.authorization_links){const el=document.createElement('div');el.className='bubble';el.append('Para revisar la información fiscal y de Buró del '+notice.context+', completa las autorizaciones requeridas: ');const link=document.createElement('a');link.href=notice.url;link.textContent='Abrir autorización en Syntage';link.target='_blank';link.rel='noopener noreferrer';el.append(link);convo.append(el);}ask('Puedes seguir con la captura mientras realizas la autorización. Escribe OK para confirmar que viste este aviso. OK no sustituye la autorización en Syntage.');return true;}
async function api(path, method='GET', data){const res=await fetch(path,{method,headers:{'Content-Type':'application/json'},credentials:'same-origin',body:data?JSON.stringify(data):undefined});const out=await res.json();if(!res.ok)throw Error(typeof out.detail==='string'?out.detail:out.detail?.[0]?.msg||'No pudimos completar la operación');return out;}
async function showOwnIntakes(){const data=await api('/intakes');if(data.has_intakes){bubble('Puedes continuar una solicitud que ya empezaste. Escribe su RFC, o el RFC de una nueva solicitud.');}else{bubble('Comencemos. ¿Cuál es el RFC del solicitante?');}state.step='rfc';field.type='text';field.value='';field.focus();}
function nextQuestion(){if(state.index>=state.questions.length){state.step='more';ask(state.nextReply||'Guardé este avance. Puedes agregar un representante, accionista o aval.');return;}const code=state.questions[state.index];state.step='answer';ask(state.questionLabels?.[code]||labels[code]||'Proporciona '+code.replaceAll('_',' '));}
async function loadIntake(id){
  const data=await api('/intakes/'+id);
  state.intake=id;
  state.questionLabels=null;
  state.nextReply=data.next_reply;state.captureStage=data.stage;
  if(data.intake.status==='submitted'){state.step='submitted';form.hidden=true;await loadDocuments();return;}
  if(data.conversation_enabled){const out=await api('/intakes/'+id+'/conversation');showConversation(out);return;}
  state.participant=null;
  state.questions=[];
  state.index=0;
  for(const person of data.participants){
    if(data.next_active_id&&person.id!==data.next_active_id)continue;
    if(!data.next_active_id&&['shareholders','guarantors'].includes(data.stage))continue;
    const done=new Set(data.answers.filter(a=>a.participant_id===person.id).map(a=>a.field_code));
    const pending=data.fields[person.id].filter(code=>!done.has(code));
    if(pending.length){state.questionLabels=data.question_labels?.[person.id];state.participant=person;state.questions=pending;bubble('Estamos completando la información de '+person.context+'.');break;}
  }
  nextQuestion();
}
ask('Hola. Soy Ana, tu asistente de First Lease. Para comenzar o continuar tu solicitud, escribe tu correo. Te enviaremos un código de un solo uso.');
field.inputMode='email';
document.querySelector('#start-application')?.addEventListener('click',()=>{document.querySelector('#welcome').hidden=true;document.querySelector('#customer-workspace').hidden=false;field.focus();});
function contactIntent(value){const text=value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();if(/\bno\s+(?:(?:quiero|necesito|deseo)\s+(?:que\s+)?)?(?:me\s+|nos\s+)?(?:contact|llam|hablar|asesor)/.test(text))return false;return /\b(contactenme|contactame|llamenme|llamame)\b|\b(me|nos)\s+(contacte|contacten|contactaran|llame|llamen|llamaran|llamas|contactas)\b|\b(quiero|necesito|quisiera|puedo|pueden|podrian|prefiero|gustaria|deseo|solicito)\b.*\b(hablar|contactar|contactarme|contacten|llamar|llamarme|llamen|asesor|asesora|asesoria)\b|\b(hablar|comunicarme)\s+(con|a)\s+(alguien|una persona|un asesor|una asesora|el equipo)\b|\b(quiero|necesito|solicito)\s+(?:un\s+)?contacto[.!?]*$/.test(text);}
const contactDialog=document.querySelector('#contact-dialog');
let contactAttempt=null,contactBusy=false,contactSent=false,contactReturnFocus=null;
function openContact(){if(!contactDialog)return;contactReturnFocus=document.activeElement;contactDialog.showModal();document.querySelector('#contact-name').focus();}
for(const button of document.querySelectorAll?.('[data-contact]')||[])button.addEventListener('click',openContact);
function closeContact(){if(contactBusy)return;contactDialog.close();contactReturnFocus?.focus();}
document.querySelector('#cancel-contact')?.addEventListener('click',closeContact);
contactDialog?.addEventListener('cancel',event=>{if(contactBusy)event.preventDefault();});
document.querySelector('#contact-form')?.addEventListener('submit',async event=>{
  event.preventDefault();if(contactBusy||contactSent)return;
  const name=document.querySelector('#contact-name').value.trim(),phone=document.querySelector('#contact-phone').value.trim(),info=document.querySelector('#contact-status');
  if(!name||!phone){info.textContent='Escribe tu nombre y teléfono.';return;}
  if(!contactAttempt||contactAttempt.name!==name||contactAttempt.phone!==phone||contactAttempt.intake_id!==state.intake)contactAttempt={request_id:crypto.randomUUID(),name,phone,intake_id:state.intake};
  contactBusy=true;const controls=['#send-contact','#cancel-contact','#contact-name','#contact-phone'].map(s=>document.querySelector(s));for(const el of controls)el.disabled=true;
  info.textContent='Registrando tu petición…';
  try{await api('/contact-requests','POST',contactAttempt);info.textContent='Listo. Tu petición quedó registrada. El equipo de First Lease te contactará al teléfono que indicaste.';contactAttempt=null;contactSent=true;document.querySelector('#send-contact').hidden=true;document.querySelector('#cancel-contact').textContent='Continuar';}
  catch(error){info.textContent=error.message;if(error.message.includes('otros datos'))contactAttempt=null;}
  finally{contactBusy=false;for(const el of controls)el.disabled=false;}
});
contactDialog?.addEventListener('close',()=>{if(contactSent){document.querySelector('#contact-name').value='';document.querySelector('#contact-phone').value='';}contactSent=false;document.querySelector('#contact-status').textContent='';document.querySelector('#send-contact').hidden=false;document.querySelector('#cancel-contact').textContent='Volver';});
let captureBusy=false,exitBusy=false;
function exitIntent(value){const text=value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim().replace(/[.!]+$/,'');return /^(salir|quiero salir|cerrar sesion|quiero cerrar sesion|continuar despues|terminar por ahora|me retiro)$/.test(text);}
async function exitSession(){
 if(exitBusy)return;
 if(captureBusy||contactBusy||(typeof documentUploadBusy!=='undefined'&&documentUploadBusy)||(typeof documentActionBusy!=='undefined'&&documentActionBusy)){status('Espera a que termine de guardarse tu información antes de salir.');return;}
 exitBusy=true;const button=document.querySelector('#exit-session');if(button)button.disabled=true;
 try{await api('/auth/logout','POST',{});document.querySelector('#customer-workspace').hidden=true;document.querySelector('#welcome').hidden=true;document.querySelector('#exit-screen').hidden=false;convo.replaceChildren();document.querySelector('#documents')?.replaceChildren();document.querySelector('#capture-progress').hidden=true;field.value='';status('');for(const key of Object.keys(state))delete state[key];Object.assign(state,{step:'email',email:'',intake:null,pendingIntakeId:null,participant:null,questions:[],index:0,pendingTurn:null,pendingChatReply:null});contactAttempt=null;document.querySelector('#contact-name').value='';document.querySelector('#contact-phone').value='';document.querySelector('#exit-title').setAttribute('tabindex','-1');document.querySelector('#exit-title').focus();}
 catch(error){status('No pudimos cerrar la sesión. '+error.message);}
 finally{exitBusy=false;if(button)button.disabled=false;}
}
document.querySelector('#exit-session')?.addEventListener('click',exitSession);
document.querySelector('#copy-conversation')?.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(convo.innerText);status('Conversación copiada. Puedes revisarla antes de compartirla.');}catch{status('No pudimos copiar. Selecciona el texto de la conversación y cópialo.');}});
document.querySelector('#undo-answer')?.addEventListener('click',async e=>{if(!['conversation','documents'].includes(state.step)){status('Abre la captura de la solicitud para deshacer una respuesta.');return;}if(typeof documentUploadBusy!=='undefined'&&documentUploadBusy){status('Espera a que termine de guardarse el archivo.');return;}const button=e.currentTarget;if(captureBusy||exitBusy)return;captureBusy=true;button.disabled=true;try{const out=await api('/intakes/'+state.intake+'/conversation/undo-last','POST',{});state.pendingTurn=null;bubble('Deshice el último cambio de captura.');showConversation(out);status('');}catch(error){status(error.message);}finally{captureBusy=false;button.disabled=false;}});
form.addEventListener('submit',async e=>{e.preventDefault();if(captureBusy||exitBusy)return;const value=field.value.trim();if(!value)return;if(exitIntent(value)){await exitSession();return;}if(contactIntent(value)){field.value='';openContact();return;}if(['email','rfc','participant_rfc'].includes(state.step)&&/^(no (lo )?tengo|no tengo (ese dato|el rfc|rfc|correo)|no lo se)$/i.test(value.normalize('NFD').replace(/[\u0300-\u036f]/g,''))){status('Puedes pedir ayuda a nuestro equipo para comenzar o retomar la solicitud.');openContact();return;}const button=form.querySelector('button[type=submit]');captureBusy=true;button.disabled=true;status('');bubble(state.step==='code'?'••••••':value,true);
  try{
    if(state.step==='email'){state.email=value;await api('/auth/start','POST',{email:value});state.step='code';ask('Escribe el código de seis dígitos que enviamos a tu correo.','text');field.inputMode='numeric';}
    else if(state.step==='code'){await api('/auth/verify','POST',{email:state.email,code:value});field.inputMode='text';await showOwnIntakes();}
    else if(state.step==='rfc'){const result=await api('/intakes','POST',{rfc:value});if(result.status==='submitted'){await loadIntake(result.id);}else{bubble('Gracias. Vamos a completar los datos de esta solicitud.');if(!authorization(result.authorization_url,result.id))await loadIntake(result.id);}}
    else if(state.step==='authorization_ack'){if(value.toUpperCase()!=='OK'){ask('Para continuar, escribe OK. La autorización se realiza por separado en la liga de Syntage.');}else{await loadIntake(state.pendingIntakeId);state.pendingIntakeId=null;}}
    else if(state.step==='conversation'){if(!state.pendingTurn||state.pendingTurn.message!==value){state.pendingTurn={request_id:crypto.randomUUID(),message:value,question:state.currentQuestion};}const out=await api('/intakes/'+state.intake+'/conversation','POST',state.pendingTurn);state.pendingTurn=null;if(!conversationAuthorization(out))showConversation(out);}
    else if(state.step==='conversation_ack'){if(value.toUpperCase()!=='OK'){ask('Escribe OK para seguir con la captura. La autorización se realiza en Syntage.');}else{showConversation(state.pendingChatReply);}}
    else if(state.step==='answer'){const result=await api(`/intakes/${state.intake}/participants/${state.participant.id}/answers`,'PUT',{field_code:state.questions[state.index],value});if(result.pending)bubble('No proporcionado: el dato queda pendiente para seguimiento. Puedes pedir que te contacten.');state.index++;if(state.index>=state.questions.length)await loadIntake(state.intake);else nextQuestion();}
    else if(state.step==='more'){const role=value.toLowerCase();if(state.captureStage==='shareholders'&&['listo accionistas','no hay accionistas con más del 10%'].includes(role)){await api('/intakes/'+state.intake+'/shareholders/complete','POST',{});await loadIntake(state.intake);}else if(state.captureStage==='guarantors'&&['listo avales','sin aval'].includes(role)){await api('/intakes/'+state.intake+'/guarantors/complete','POST',{});await loadIntake(state.intake);}else if(!['aval','representante','accionista'].includes(role)){ask(state.nextReply||'Para agregar a alguien escribe representante, accionista o aval.');}else{state.newRole=role;state.step='participant_rfc';ask('¿Cuál es el RFC del '+role+'?');}}
    else if(state.step==='participant_rfc'){const result=await api('/intakes/'+state.intake+'/participants','POST',{role:state.newRole,rfc:value});if(!authorization(result.authorization_url,state.intake))await loadIntake(state.intake);}
  }catch(err){status(err.message);field.value=value;}finally{captureBusy=false;button.disabled=false;}
});
