const convo = document.querySelector('#conversation');
const form = document.querySelector('#composer');
const field = document.querySelector('#answer');
const prompt = document.querySelector('#prompt');
const notice = document.querySelector('#notice');
const state = {step:'email', email:'', intake:null, participant:null, questions:[], index:0};
const labels = {razon_social:'¿Cuál es la razón social?', nombre:'¿Cuál es el nombre completo?', nombre_comercial:'¿Cuál es el nombre comercial?', pagina_web:'¿Cuál es su página web?', correo_contacto:'¿Cuál es el correo de contacto?', telefono:'¿Cuál es el teléfono?', actividad:'¿A qué se dedica?', ocupacion:'¿Cuál es su ocupación?',cargo:'¿Cuál es su cargo?'};
function bubble(value,mine=false){const el=document.createElement('div');el.className='bubble'+(mine?' mine':'');el.textContent=value;convo.append(el);el.scrollIntoView({block:'end'});}
function ask(value,type='text'){bubble(value);prompt.textContent='Tu respuesta';field.type=type;field.value='';field.focus();}
function status(value){notice.textContent=value;}
async function api(path, method='GET', data){const res=await fetch(path,{method,headers:{'Content-Type':'application/json'},credentials:'same-origin',body:data?JSON.stringify(data):undefined});const out=await res.json();if(!res.ok)throw Error(out.detail||'No pudimos completar la operación');return out;}
async function showOwnIntakes(){const data=await api('/intakes');if(data.has_intakes){bubble('Puedes continuar una solicitud que ya empezaste. Escribe su RFC, o el RFC de una nueva solicitud.');}else{bubble('Comencemos. ¿Cuál es el RFC del solicitante?');}state.step='rfc';field.type='text';field.value='';field.focus();}
function nextQuestion(){if(state.index>=state.questions.length){state.step='more';ask('Guardé este avance. Si deseas agregar un aval, escribe “aval”. Si deseas agregar un representante, escribe “representante”. También puedes salir y regresar después.');return;}const code=state.questions[state.index];state.step='answer';ask(labels[code]||'Proporciona '+code.replaceAll('_',' '));}
async function loadIntake(id){
  const data=await api('/intakes/'+id);
  state.intake=id;
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
form.addEventListener('submit',async e=>{e.preventDefault();const value=field.value.trim();if(!value)return;const button=form.querySelector('button');button.disabled=true;status('');bubble(['code','answer'].includes(state.step)?'••••••':value,true);
  try{
    if(state.step==='email'){state.email=value;await api('/auth/start','POST',{email:value});state.step='code';ask('Escribe el código de seis dígitos que enviamos a tu correo.','text');field.inputMode='numeric';}
    else if(state.step==='code'){await api('/auth/verify','POST',{email:state.email,code:value});field.inputMode='text';await showOwnIntakes();}
    else if(state.step==='rfc'){const result=await api('/intakes','POST',{rfc:value});bubble('Gracias. Vamos a completar los datos de esta solicitud.');await loadIntake(result.id);}
    else if(state.step==='answer'){await api(`/intakes/${state.intake}/participants/${state.participant.id}/answers`,'PUT',{field_code:state.questions[state.index],value});state.index++;if(state.index>=state.questions.length)await loadIntake(state.intake);else nextQuestion();}
    else if(state.step==='more'){const role=value.toLowerCase();if(!['aval','representante'].includes(role)){ask('Tu avance está guardado. Para agregar a alguien escribe “aval” o “representante”.');}else{state.newRole=role;state.step='participant_rfc';ask(role==='aval'?'¿Cuál es el RFC del aval?':'¿Cuál es el RFC del representante?');}}
    else if(state.step==='participant_rfc'){await api('/intakes/'+state.intake+'/participants','POST',{role:state.newRole,rfc:value});await loadIntake(state.intake);}
  }catch(err){status(err.message);field.value=value;}finally{button.disabled=false;}
});
