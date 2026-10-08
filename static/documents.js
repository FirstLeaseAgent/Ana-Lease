const documentPanel=document.querySelector('#documents');
const fileAttempts=new Map();
let documentUploadBusy=false,documentActionBusy=false;
function sendDocument(path,file,onProgress){return new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();xhr.open('PUT',path);xhr.withCredentials=true;xhr.timeout=75000;
  xhr.setRequestHeader('Content-Type',file.type||'application/octet-stream');
  xhr.upload.onprogress=e=>{if(e.lengthComputable)onProgress(Math.round(e.loaded/e.total*100));};
  xhr.onload=()=>{let out;try{out=JSON.parse(xhr.responseText);}catch{reject(Error('No pudimos confirmar el guardado. Vuelve a intentar con el mismo archivo.'));return;}
    if(xhr.status<200||xhr.status>=300||out.ok!==true){reject(Error(out.detail||'No pudimos guardar el archivo. Vuelve a intentar.'));return;}resolve(out);};
  xhr.onerror=xhr.ontimeout=()=>reject(Error('No pudimos confirmar el guardado. Reintenta con el mismo archivo.'));
  xhr.send(file);
});}
function docElement(tag,text){const el=document.createElement(tag);if(text)el.textContent=text;return el;}
function docButton(text,action){const b=docElement('button',text);b.type='button';b.addEventListener('click',async()=>{if(documentUploadBusy||documentActionBusy)return;documentActionBusy=true;b.disabled=true;status('');try{await action();}catch(error){status(error.message);}finally{documentActionBusy=false;b.disabled=false;}});return b;}
async function loadDocuments(){
  const result=await api('/intakes/'+state.intake+'/documents');
  documentPanel.replaceChildren();documentPanel.hidden=false;
  documentPanel.append(docElement('h2','Documentos'),docElement('p',result.message));
  const submitted=result.status==='submitted';
  if(result.pending_data)documentPanel.append(docElement('p','Datos no proporcionados: '+result.pending_data+'. Quedan pendientes para seguimiento del equipo.'));
  const required=result.documents.filter(r=>r.required&&r.applicable!==false).length;
  setCaptureProgress(submitted?'Solicitud recibida · documentos':'Documentos obligatorios',Math.max(0,required-result.required_missing),required,submitted?'Recibida · pendiente de revisión':(result.pending_data?'Captura recorrida · '+result.pending_data+' datos pendientes → Documentos':'Datos completos → Documentos → Finalización'));
  if(submitted){state.step='submitted';form.hidden=true;documentPanel.className='documents-complete';}else documentPanel.className='';
  if(!result.upload_available)documentPanel.append(docElement('p','La carga de archivos estará disponible en breve. Puedes revisar la lista y dejar pendientes.'));
  const options=docElement('div');options.className='document-actions';
  for(const role of ['aval','representante'])options.append(docButton('Agregar otro '+role,async()=>{const out=await api('/intakes/'+state.intake+'/conversation','POST',{request_id:crypto.randomUUID(),message:'Quiero agregar un '+role,question:state.currentQuestion});showConversation(out);}));
  if(!submitted)documentPanel.append(options);
  let current='';
  for(const item of result.documents){
    if(item.participant_id!==current){documentPanel.append(docElement('h3',item.participant));current=item.participant_id;}
    const card=docElement('article');card.className='document-card';
    card.append(docElement('strong',item.label),docElement('p',item.required?'Obligatorio':'Opcional'));
    if(item.members?.length>1)card.append(docElement('p','Un solo archivo cubre estos roles de la misma persona: '+item.members.map(m=>m.participant.split(' · ')[0]).join(', ')+'.'));
    const labels={received:'Recibido · pendiente de revisión',pending:'Pendiente',deferred:'Pendiente · lo entregarás después',not_applicable:'No aplica'};
    card.append(docElement('p',labels[item.status]));
    const base='/intakes/'+state.intake+'/documents/'+item.participant_id+'/'+item.code;
    if(item.applicable===null){
      const label=docElement('label',item.dependency_question||'Indica '+item.dependency_field.replace(/_/g,' ')+':');
      const choices=item.dependency_options||[];const input=docElement(choices.length?'select':'input');if(choices.length)for(const value of ['',...choices]){const option=docElement('option',value||'Selecciona una opción');option.value=value;input.append(option);}label.append(input);card.append(label);
      card.append(docButton('Guardar dato',async()=>{if(!input.value.trim())throw Error('Indica el estado civil');await api('/intakes/'+state.intake+'/documents/dependency','POST',{participant_id:item.participant_id,field:item.dependency_field,value:input.value.trim()});await loadDocuments();}));
    }
    if(item.applicable===true&&(!submitted||item.status!=='received')){
      let selectedFile=null;
      const file=docElement('input');file.type='file';file.accept='.pdf,.jpg,.jpeg,.png';file.hidden=true;file.setAttribute('aria-label','Seleccionar '+item.label);file.disabled=!result.upload_available;
      const drop=docElement('div');drop.className='drop-zone';
      drop.append(docElement('strong','Arrastra tu archivo aquí'),docElement('p','PDF, JPG o PNG · máximo 10 MB'));
      const selectedLabel=docElement('p','También puedes elegirlo desde tu equipo.');selectedLabel.className='selected-file';selectedLabel.setAttribute('aria-live','polite');
      const selectFiles=files=>{if(documentUploadBusy||!result.upload_available)return;if(files.length!==1){status('Selecciona un solo archivo para este documento.');return;}const selected=files[0];if(!selected.size||selected.size>10*1024*1024){status('Selecciona un archivo de hasta 10 MB que no esté vacío.');return;}if(!/\.(pdf|jpe?g|png)$/i.test(selected.name)){status('Selecciona un PDF, JPG o PNG.');return;}selectedFile=selected;selectedLabel.textContent=selected.name+' · '+(selected.size/1024/1024).toFixed(2)+' MB';status('');};
      file.addEventListener('change',()=>selectFiles(file.files));
      for(const name of ['dragenter','dragover'])drop.addEventListener(name,e=>{e.preventDefault();if(result.upload_available&&!documentUploadBusy)drop.className='drop-zone drag-active';});
      drop.addEventListener('dragleave',()=>{drop.className='drop-zone';});
      drop.addEventListener('drop',e=>{e.preventDefault();drop.className='drop-zone';selectFiles(e.dataTransfer.files);});
      const choose=docButton('Elegir archivo',async()=>file.click());choose.className='secondary';choose.disabled=!result.upload_available;
      drop.append(file,choose,selectedLabel);card.append(drop);
      const progress=docElement('progress');progress.max=100;progress.value=0;progress.hidden=true;progress.setAttribute('aria-label','Progreso de envío de '+item.label);
      const uploadStatus=docElement('p');uploadStatus.className='upload-status';uploadStatus.setAttribute('role','status');card.append(progress,uploadStatus);
      const upload=docButton(item.status==='received'?'Subir nueva versión':'Subir archivo',async()=>{
        const selected=selectedFile;if(!selected)throw Error('Selecciona o arrastra un archivo');
        if(selected.size>10*1024*1024)throw Error('El archivo pesa más de 10 MB');
        const key=state.intake+'/'+item.participant_id+'/'+item.code;
        let attempt=fileAttempts.get(key);
        if(!attempt||attempt.file!==selected){attempt={file:selected,id:crypto.randomUUID()};fileAttempts.set(key,attempt);}
        documentUploadBusy=true;documentPanel.setAttribute('aria-busy','true');
        const controls=[...documentPanel.querySelectorAll('button,input,select')].map(el=>[el,el.disabled]);for(const [el] of controls)el.disabled=true;
        progress.hidden=false;progress.value=0;uploadStatus.textContent='Enviando archivo…';
        try{await sendDocument(base+'/uploads/'+attempt.id,selected,value=>{progress.value=value;uploadStatus.textContent=value<100?'Enviando archivo · '+value+'%':'Archivo enviado. Guardando en SharePoint…';});
          fileAttempts.delete(key);uploadStatus.textContent='Archivo recibido · pendiente de revisión';
        }catch(error){uploadStatus.textContent=error.message;throw error;}
        finally{documentUploadBusy=false;documentPanel.setAttribute('aria-busy','false');for(const [el,disabled] of controls)el.disabled=disabled;}
        await loadDocuments();status('Archivo recibido y guardado en SharePoint. Pendiente de revisión.');
      });upload.disabled=!result.upload_available;card.append(upload);
      for(const source of item.reuse_sources)card.append(docButton('Usar el documento de '+source.label,async()=>{await api(base+'/reuse','POST',{source_participant_id:source.participant_id});await loadDocuments();}));
    }
    if(!submitted&&item.applicable!==false&&item.status!=='received')card.append(docButton('No lo tengo ahora',async()=>{await api(base+'/defer','POST',{});await loadDocuments();}));
    documentPanel.append(card);
  }
  if(!submitted){
    documentPanel.append(docElement('p',result.required_missing?'Documentos requeridos pendientes: '+result.required_missing+'. Puedes finalizar ahora; el equipo podrá dar seguimiento a los faltantes.':'Los documentos obligatorios están recibidos. Los opcionales no impiden finalizar.'));
    documentPanel.append(docElement('p','Al finalizar se enviará la solicitud a revisión y se cerrará la captura de datos. Podrás volver para subir documentos faltantes. Los documentos faltantes seguirán pendientes; la solicitud todavía no está aprobada.'));
    const finish=docButton('Finalizar solicitud',async()=>{await api('/intakes/'+state.intake+'/finalize','POST',{});await loadDocuments();});
    documentPanel.append(finish);
  }
}
