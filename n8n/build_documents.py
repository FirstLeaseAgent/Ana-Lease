"""Isolated upload workflow; select the existing SharePoint credential after import."""
import json
from pathlib import Path

def node(name,kind,version,x,y,parameters,**extra):
    return {'id':name.lower().replace(' ','-'),'name':name,'type':'n8n-nodes-base.'+kind,
            'typeVersion':version,'position':[x,y],'parameters':parameters,**extra}

prepare = """
const body=$input.first().json.body;
const NodeBuffer=Reflect.get(globalThis,'Buffer');
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const mime=new Map([['application/pdf','pdf'],['image/jpeg','jpg'],['image/png','png']]);
const fail=()=>[{json:{ok:false,version:1,error:'invalid_file'}}];
if(!body||body.version!==1)return fail();
if(!NodeBuffer||typeof NodeBuffer.from!=='function')throw new Error('Buffer no está disponible en este entorno');
const rfc=typeof body.rfc==='string'?body.rfc.trim().toUpperCase():'';
if(!/^[A-ZÑ&]{3,4}\\d{6}[A-Z0-9]{3}$/.test(rfc))return fail();
if(!body||body.version!==1||!['upload_id','intake_id','participant_id'].every(k=>typeof body[k]==='string'&&uuid.test(body[k]))||
 typeof body.document_code!=='string'||!/^[a-z_]{1,80}$/.test(body.document_code)||!mime.has(body.mime_type)||
 typeof body.content_base64!=='string'||body.content_base64.length>13981016||!body.content_base64.length||
 !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(body.content_base64))return fail();
const data=NodeBuffer.from(body.content_base64,'base64');
if(data.length>10485760||!data.length)return fail();
let actual='';
if(data.subarray(0,5).equals(NodeBuffer.from('%PDF-')))actual='application/pdf';
if(data.subarray(0,3).equals(NodeBuffer.from([255,216,255])))actual='image/jpeg';
if(data.subarray(0,8).equals(NodeBuffer.from([137,80,78,71,13,10,26,10])))actual='image/png';
if(!actual||actual!==body.mime_type)return fail();
const extension=mime.get(actual);
if(!extension)return fail();
const filename=rfc+'-'+body.document_code.replace(/_/g,'-')+'-por-revisar-'+body.upload_id+'.'+extension;
const output={json:{ok:true,file_name:filename,upload_id:body.upload_id},binary:{data:{data:body.content_base64,fileName:filename,fileExtension:extension,mimeType:actual}}};
return [output];
"""
confirm="""
const response=$input.first().json;
if(response.error||typeof response.id!=='string'||!response.id||response.id.length>500)
 return [{json:{ok:false,version:1,error:'storage_unavailable'}}];
return [{json:{ok:true,version:1,storage_id:response.id}}];
"""
nodes=[
 node('Entrada documento','webhook',2,0,0,{'httpMethod':'POST','path':'analease-documents','authentication':'headerAuth','responseMode':'responseNode','options':{}}),
 node('Preparar archivo','code',2,240,0,{'jsCode':prepare}),
 node('Archivo válido','if',2.2,480,0,{'conditions':{'options':{'caseSensitive':True,'leftValue':'','typeValidation':'strict','version':2},'conditions':[{'id':'valid','leftValue':'={{ $json.ok }}','rightValue':True,'operator':{'type':'boolean','operation':'true','singleValue':True}}],'combinator':'and'},'options':{}}),
 node('Guardar en SharePoint','microsoftSharePoint',1,720,-80,{'resource':'file','operation':'upload','site':{'__rl':True,'value':'','mode':'list'},'folder':{'__rl':True,'value':'','mode':'list'},'fileName':'={{ $json.file_name }}','fileContents':'data'},onError='continueRegularOutput',alwaysOutputData=True),
 node('Confirmar guardado','code',2,960,-80,{'jsCode':confirm}),
 node('Responder documento','respondToWebhook',1.4,1200,0,{'respondWith':'json','responseBody':'={{ JSON.stringify($json) }}','options':{'responseCode':"={{ $json.ok ? 200 : ($json.error === 'invalid_file' ? 400 : 502) }}"}}),
]
connections={}
def link(source,target,index=0):
    outputs=connections.setdefault(source,{'main':[]})['main']
    while len(outputs)<=index:outputs.append([])
    outputs[index].append({'node':target,'type':'main','index':0})
link('Entrada documento','Preparar archivo');link('Preparar archivo','Archivo válido')
link('Archivo válido','Guardar en SharePoint');link('Archivo válido','Responder documento',1)
link('Guardar en SharePoint','Confirmar guardado');link('Confirmar guardado','Responder documento')
workflow={'name':'AnaLease - Documentos SharePoint (piloto)','nodes':nodes,'connections':connections,'active':False,
 'settings':{'executionOrder':'v1','saveDataSuccessExecution':'none','saveDataErrorExecution':'none','saveManualExecutions':False,'saveExecutionProgress':False}}
Path(__file__).with_name('AnaLease-documentos-SharePoint.json').write_text(json.dumps(workflow,ensure_ascii=False,indent=2)+'\n')
