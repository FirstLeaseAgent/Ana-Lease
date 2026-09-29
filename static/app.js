const convo = document.querySelector('#conversation');
const form = document.querySelector('#composer');
const field = document.querySelector('#answer');
const prompt = document.querySelector('#prompt');
const notice = document.querySelector('#notice');
const state = {step:'email', email:'', intake:null, pendingIntakeId:null, participant:null, questions:[], index:0, pendingTurn:null, pendingChatReply:null};
const labels = {razon_social:'¿Cuál es la razón social?', nombre:'¿Cuál es el nombre completo?', nombre_comercial:'¿Cuál es el nombre comercial?', pagina_web:'¿Cuál es su página web?', correo_contacto:'¿Cuál es el correo de contacto?', telefono:'¿Cuál es el teléfono?', actividad:'¿A qué se dedica?', ocupacion:'¿Cuál es su ocupación?',cargo:'¿Cuál es su cargo?'};
function bubble(value,mine=false){const el=document.createElement('div');el.className='bubble'+(mine?' mine':'');el.textContent=value;convo.append(el);el.scrollIntoView({block:'end'});}
function authorization(url,id){if(!url)return false;state.pendingIntakeId=id;state.step='authorization_ack';const el=document.createElement('div');el.className='bubble';el.append('Para poder revisar tu información fiscal y de Buró de Crédito, es necesario completar las autorizaciones requeridas en Syntage: ');const link=document.createElement('a');link.href=url;link.textContent='Abrir autorización en Syntage';link.target='_blank';link.rel='noopener noreferrer';el.append(link);convo.append(el);ask('Puedes seguir capturando tus datos mientras realizas la autorización. Escribe OK para confirmar que viste este aviso y continuar. OK no sustituye la autorización en Syntage.');return true;}
function ask(value,type='text'){bubble(value);prompt.textContent='Tu respuesta';field.type=type;field.value='';field.focus();}
function status(value){notice.textContent=value;}
function showConversation(out){state.step='conversation';state.pendingChatReply=null;ask(out.reply);}
function conversationAuthorization(out){if(!out.authorization_links?.length)return false;state.pendingChatReply=out;state.step='conversation_ack';for(const notice of out.authorization_links){const el=document.createElement('div');el.className='bubble';el.append('Para revisar la información fiscal y de Buró del '+notice.context+', completa las autorizaciones requeridas: ');const link=document.createElement('a');link.href=notice.url;link.textContent='Abrir autorización en Syntage';link.target='_blank';link.rel='noopener noreferrer';el.append(link);convo.append(el);}ask('Puedes seguir con la captura mientras realizas la autorización. Escribe OK para confirmar que viste este aviso. OK no sustituye la autorización en Syntage.');return true;}
async function api(path, method='GET', data){const res=await fetch(path,{method,headers:{'Content-Type':'application/json'},credentials:'same-origin',body:data?JSON.stringify(data):undefined});const out=await res.json();if(!res.ok)throw Error(out.detail||'No pudimos completar la operación');return out;}
async function showOwnIntakes(){const data=await api('/intakes');if(data.has_intakes){bubble('Puedes continuar una solicitud que ya empezaste. Escribe su RFC, o el RFC de una nueva solicitud.');}else{bubble('Comencemos. ¿Cuál es el RFC del solicitante?');}state.step='rfc';field.type='text';field.value='';field.focus();}
function nextQuestion(){if(state.index>=state.questions.length){state.step='more';ask('Guardé este avance. Si deseas agregar un aval, escribe “aval”. Si deseas agregar un representante, escribe “representante”. También puedes salir y regresar después.');return;}const code=state.questions[state.index];state.step='answer';ask(labels[code]||'Proporciona '+code.replaceAll('_',' '));}
async function loadIntake(id){
  const data=await api('/intakes/'+id);
  state.intake=id;
  if(data.conversation_enabled){const out=await api('/intakes/'+id+'/conversation');showConversation(out);return;}
  state.participant=null;
  state.questions=[];
  state.index=0;
  for(const person of data.participants){
    const done=new Set(data.answers.filter(a=>a.participant_id===person.id).map(a=>a.field_code));
    const pending=data.fields[person.id].filter(code=>!done.has(code));
    if(pending.length){state.participant=person;state.questions=pending;bubble('Estamos completando la información de '+person.context+'.');break;}
  }
  nextQuestion();
}
ask('Hola. Para continuar, escribe tu correo. Te enviaremos un código de un solo uso.','email');
form.addEventListener('submit',async e=>{e.preventDefault();const value=field.value.trim();if(!value)return;const button=form.querySelector('button');button.disabled=true;status('');bubble(['code','answer','conversation'].includes(state.step)?'••••••':value,true);
  try{
    if(state.step==='email'){state.email=value;await api('/auth/start','POST',{email:value});state.step='code';ask('Escribe el código de seis dígitos que enviamos a tu correo.','text');field.inputMode='numeric';}
    else if(state.step==='code'){await api('/auth/verify','POST',{email:state.email,code:value});field.inputMode='text';await showOwnIntakes();}
    else if(state.step==='rfc'){const result=await api('/intakes','POST',{rfc:value});bubble('Gracias. Vamos a completar los datos de esta solicitud.');if(!authorization(result.authorization_url,result.id))await loadIntake(result.id);}
    else if(state.step==='authorization_ack'){if(value.toUpperCase()!=='OK'){ask('Para continuar, escribe OK. La autorización se realiza por separado en la liga de Syntage.');}else{await loadIntake(state.pendingIntakeId);state.pendingIntakeId=null;}}
    else if(state.step==='conversation'){if(!state.pendingTurn||state.pendingTurn.message!==value){state.pendingTurn={request_id:crypto.randomUUID(),message:value};}const out=await api('/intakes/'+state.intake+'/conversation','POST',state.pendingTurn);state.pendingTurn=null;if(!conversationAuthorization(out))showConversation(out);}
    else if(state.step==='conversation_ack'){if(value.toUpperCase()!=='OK'){ask('Escribe OK para seguir con la captura. La autorización se realiza en Syntage.');}else{showConversation(state.pendingChatReply);}}
    else if(state.step==='answer'){await api(`/intakes/${state.intake}/participants/${state.participant.id}/answers`,'PUT',{field_code:state.questions[state.index],value});state.index++;if(state.index>=state.questions.length)await loadIntake(state.intake);else nextQuestion();}
    else if(state.step==='more'){const role=value.toLowerCase();if(!['aval','representante'].includes(role)){ask('Tu avance está guardado. Para agregar a alguien escribe “aval” o “representante”.');}else{state.newRole=role;state.step='participant_rfc';ask(role==='aval'?'¿Cuál es el RFC del aval?':'¿Cuál es el RFC del representante?');}}
    else if(state.step==='participant_rfc'){const result=await api('/intakes/'+state.intake+'/participants','POST',{role:state.newRole,rfc:value});if(!authorization(result.authorization_url,state.intake))await loadIntake(state.intake);}
  }catch(err){status(err.message);field.value=value;}finally{button.disabled=false;}
});
