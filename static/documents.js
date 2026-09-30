const documentPanel=document.querySelector('#documents');
const fileAttempts=new Map();
function docElement(tag,text){const el=document.createElement(tag);if(text)el.textContent=text;return el;}
function docButton(text,action){const b=docElement('button',text);b.type='button';b.addEventListener('click',async()=>{b.disabled=true;status('');try{await action();}catch(error){status(error.message);}finally{b.disabled=false;}});return b;}
async function loadDocuments(){
  const result=await api('/intakes/'+state.intake+'/documents');
  documentPanel.replaceChildren();documentPanel.hidden=false;
  documentPanel.append(docElement('h2','Documentos'),docElement('p',result.message));
  const submitted=result.status==='submitted';
  if(submitted){state.step='submitted';form.hidden=true;}
  if(!submitted&&!result.upload_available)documentPanel.append(docElement('p','La carga de archivos estará disponible en breve. Puedes revisar la lista y dejar pendientes.'));
  const options=docElement('div');options.className='document-actions';
  for(const role of ['aval','representante'])options.append(docButton('Agregar otro '+role,async()=>{const out=await api('/intakes/'+state.intake+'/conversation','POST',{request_id:crypto.randomUUID(),message:'Quiero agregar un '+role,question:state.currentQuestion});showConversation(out);}));
  if(!submitted)documentPanel.append(options);
  let current='';
  for(const item of result.documents){
    if(item.participant_id!==current){documentPanel.append(docElement('h3',item.participant));current=item.participant_id;}
    const card=docElement('article');card.className='document-card';
    card.append(docElement('strong',item.label),docElement('p',item.required?'Obligatorio':'Opcional'));
    const labels={received:'Recibido · pendiente de revisión',pending:'Pendiente',deferred:'Pendiente · lo entregarás después',not_applicable:'No aplica'};
    card.append(docElement('p',labels[item.status]));
    const base='/intakes/'+state.intake+'/documents/'+item.participant_id+'/'+item.code;
    if(!submitted&&item.applicable===null){
      const label=docElement('label','Para saber si aplica el acta de matrimonio, indica el estado civil:');
      const input=docElement('select');for(const value of ['', 'Casado','Soltero','Divorciado','Viudo','Unión libre']){const option=docElement('option',value||'Selecciona una opción');option.value=value;input.append(option);}label.append(input);card.append(label);
      card.append(docButton('Guardar estado civil',async()=>{if(!input.value.trim())throw Error('Indica el estado civil');await api('/intakes/'+state.intake+'/documents/dependency','POST',{participant_id:item.participant_id,field:item.dependency_field,value:input.value.trim()});await loadDocuments();}));
    }
    if(!submitted&&item.applicable===true){
      const file=docElement('input');file.type='file';file.accept='.pdf,.jpg,.jpeg,.png';file.setAttribute('aria-label','Seleccionar '+item.label);file.disabled=!result.upload_available;
      card.append(file);
      const upload=docButton(item.status==='received'?'Subir nueva versión':'Subir archivo',async()=>{
        const selected=file.files[0];if(!selected)throw Error('Selecciona un archivo');
        if(selected.size>10*1024*1024)throw Error('El archivo pesa más de 10 MB');
        const key=state.intake+'/'+item.participant_id+'/'+item.code;
        let attempt=fileAttempts.get(key);
        if(!attempt||attempt.file!==selected){attempt={file:selected,id:crypto.randomUUID()};fileAttempts.set(key,attempt);}
        const response=await fetch(base+'/uploads/'+attempt.id,{method:'PUT',credentials:'same-origin',headers:{'Content-Type':selected.type||'application/octet-stream'},body:selected});
        const out=await response.json();if(!response.ok)throw Error(out.detail||'No pudimos guardar el archivo');
        fileAttempts.delete(key);await loadDocuments();
      });upload.disabled=!result.upload_available;card.append(upload);
      for(const source of item.reuse_sources)card.append(docButton('Usar el documento de '+source.label,async()=>{await api(base+'/reuse','POST',{source_participant_id:source.participant_id});await loadDocuments();}));
    }
    if(!submitted&&item.applicable!==false&&item.status!=='received')card.append(docButton('No lo tengo ahora',async()=>{await api(base+'/defer','POST',{});await loadDocuments();}));
    documentPanel.append(card);
  }
  if(!submitted){
    documentPanel.append(docElement('p',result.required_missing?'Pendientes obligatorios: '+result.required_missing+'. Tu avance está guardado; puedes regresar después.':'Los documentos obligatorios están recibidos. Los opcionales no impiden finalizar.'));
    documentPanel.append(docElement('p','Al finalizar se cerrará la captura. Los documentos quedan pendientes de revisión; la solicitud todavía no está aprobada.'));
    const finish=docButton('Finalizar solicitud',async()=>{await api('/intakes/'+state.intake+'/finalize','POST',{});await loadDocuments();});
    finish.disabled=result.required_missing>0;documentPanel.append(finish);
  }
}
