(() => {
 const dialog=document.querySelector('#quote-dialog'), form=document.querySelector('#quote-form');
 if(!dialog||!form)return;
 const info=document.querySelector('#quote-status'), preview=document.querySelector('#quote-preview');
 const submit=document.querySelector('#quote-submit'), back=document.querySelector('#quote-back');
 const download=document.querySelector('#quote-download');
 let busy=false, confirmed=null, attempt=null, objectUrl=null, returnFocus=null;
 const money=n=>n.toLocaleString('es-MX',{style:'currency',currency:'MXN'});
 function clear(){submit.hidden=false;confirmed=null;preview.hidden=true;submit.textContent='Revisar cotización';download.hidden=true;}
 document.querySelector('#start-quote').addEventListener('click',()=>{returnFocus=document.activeElement;dialog.showModal();document.querySelector('#quote-name').focus();});
 back.addEventListener('click',()=>{if(!busy)dialog.close();});
 dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
 dialog.addEventListener('close',()=>returnFocus?.focus());
 form.addEventListener('input',()=>{if(!busy){clear();info.textContent='';}});
 form.addEventListener('submit',async e=>{
  e.preventDefault();if(busy)return;
  const data={name:document.querySelector('#quote-name').value.trim(),phone:document.querySelector('#quote-phone').value.trim(),asset:document.querySelector('#quote-asset').value.trim(),value:document.querySelector('#quote-value').value.trim(),down_payment:document.querySelector('#quote-down').value.trim()};
  const signature=JSON.stringify(data);
  if(!attempt||attempt.signature!==signature)attempt={signature,request_id:crypto.randomUUID()};
  data.request_id=attempt.request_id;
  busy=true;for(const el of form.querySelectorAll('input,button'))el.disabled=true;
  info.textContent=confirmed===signature?'Generando tu PDF y guardando la cotización…':'Revisando tus datos…';
  try{
   const generating=confirmed===signature;
   const res=await fetch(generating?'/quotes':'/quotes/preview',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
   if(!res.ok){const out=await res.json();throw Error(typeof out.detail==='string'?out.detail:out.detail?.[0]?.msg||'No pudimos completar la cotización.');}
   if(!generating){const out=await res.json();preview.textContent='Valor del activo: '+money(out.value)+'. Enganche: '+money(out.amount)+' ('+out.percentage.toLocaleString('es-MX',{maximumFractionDigits:4})+'%). La cotización incluirá todos los plazos disponibles.';preview.hidden=false;confirmed=signature;submit.textContent='Confirmar y generar PDF';info.textContent='Revisa el enganche antes de confirmar.';}
   else{const blob=await res.blob();if(!blob.size||blob.type!=='application/pdf')throw Error('No recibimos un PDF válido. Solicita que te contacten.');if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=URL.createObjectURL(blob);download.href=objectUrl;download.download='FirstLease-Cotizacion.pdf';download.hidden=false;submit.hidden=true;info.textContent='Tu cotización está lista. Descarga el PDF para consultar todos los plazos.';}
  }catch(error){info.textContent=error.message;}
  finally{busy=false;for(const el of form.querySelectorAll('input,button'))el.disabled=false;}
 });
 dialog.addEventListener('close',()=>{if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl=null;form.reset();attempt=null;clear();submit.hidden=false;info.textContent='';}});
 document.querySelector('#quote-contact').addEventListener('click',()=>{if(!busy){dialog.close();openContact();}});
})();
