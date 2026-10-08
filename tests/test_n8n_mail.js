const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const workflow=JSON.parse(fs.readFileSync('n8n/AnaLease - envio de codigo.json','utf8'));
const mail=workflow.nodes.find(n=>n.name==='Enviar con Outlook').parameters;
const evaluate=(key,body)=>vm.runInNewContext(mail[key].slice(3,-3),{$json:{body}});
const contact={event:'contact_requested',name:'Persona de prueba',phone:'5512345678',contact_id:'test-id',email:'other@example.test'};
assert.equal(evaluate('toRecipients',contact),'aortega@firstlease.com.mx');assert.equal(evaluate('subject',contact),'AnaLease · Solicitud de contacto');assert.ok(evaluate('bodyContent',contact).includes('Nombre: Persona de prueba\nTeléfono: 5512345678'));assert.ok(evaluate('bodyContent',contact).includes('seguimiento?contacto=test-id'));assert.ok(!evaluate('bodyContent',contact).includes('undefined'));
assert.equal(evaluate('toRecipients',{event:'submitted',email:'other@example.test'}),'aortega@firstlease.com.mx');assert.equal(evaluate('subject',{event:'submitted'}),'AnaLease · Solicitud recibida');assert.ok(evaluate('bodyContent',{event:'submitted',intake_id:'test-id',required_missing:2}).includes('pendientes: 2'));
assert.equal(evaluate('toRecipients',{email:'persona@example.test',code:'012345'}),'persona@example.test');assert.ok(evaluate('bodyContent',{code:'012345'}).includes('012345'));assert.equal(mail.additionalFields.bodyContentType,'Text');
console.log('Mail routes contact, submission and OTP independently');
