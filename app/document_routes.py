"""Owner-scoped document checklist and SharePoint upload bridge."""
import hashlib
import logging
import time
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

from .conversation import capture_stage
from . import documents as d

logger = logging.getLogger(__name__)


class DependencyInput(BaseModel):
    participant_id: UUID
    field: str = Field(max_length=80)
    value: str = Field(min_length=1,max_length=80)


class ReuseDocumentInput(BaseModel):
    source_participant_id: UUID


def install(app, pool, owner, intake_for_owner, conversation_people, enabled):
    def scope(conn,intake_id,user_id,ready=True,editable=True):
        intake_for_owner(conn,intake_id,user_id,editable=editable)
        if not enabled(conn,user_id):
            raise HTTPException(404,'Documentos no disponibles')
        people = conversation_people(conn,intake_id)
        if not next(iter(people.values()),{}).get('catalog',{}).get('documents',d.CATALOG):raise HTTPException(503,'No pudimos cargar los requisitos documentales; intenta más tarde')
        if ready and capture_stage(people) != 'documents':
            raise HTTPException(409,'Completa los datos de los participantes antes de continuar con documentos')
        deps = conn.execute('SELECT participant_id,field_code,value FROM document_dependencies WHERE intake_id=%s',(intake_id,)).fetchall()
        requirements = d.requirements(people,{(str(row['participant_id']),row['field_code']):row['value'] for row in deps})
        return people,requirements

    def requirement(rows,participant_id,code,applicable=True):
        row = next((r for r in rows if r['participant_id']==str(participant_id) and r['code']==code),None)
        if row is None:
            raise HTTPException(404,'Documento no encontrado en esta solicitud')
        if applicable and row['applicable'] is not True:
            raise HTTPException(409,'Primero completa el dato que determina si este documento aplica')
        return row

    def event(conn,intake_id,user_id,pid,code,action,details=None):
        conn.execute('INSERT INTO document_events(intake_id,actor_id,participant_id,document_code,action,details) VALUES(%s,%s,%s,%s,%s,%s)',
                     (intake_id,user_id,pid,code,action,Jsonb(details or {})))

    def assign(conn,intake_id,pid,code,status,upload_id=None):
        conn.execute('INSERT INTO document_states(intake_id,participant_id,document_code,status,upload_id) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(participant_id,document_code) DO UPDATE SET status=EXCLUDED.status,upload_id=EXCLUDED.upload_id,updated_at=now()',
                     (intake_id,pid,code,status,upload_id))

    @app.get('/intakes/{intake_id}/documents')
    def checklist(intake_id:UUID,request:Request):
        user_id=owner(request)
        with pool.connection() as conn:
            intake=intake_for_owner(conn,intake_id,user_id)
            people,rows=scope(conn,intake_id,user_id,editable=False)
            states=conn.execute('SELECT participant_id,document_code,status,upload_id FROM document_states WHERE intake_id=%s',(intake_id,)).fetchall()
        rows=d.grouped_requirements(rows,people,states)
        missing=d.required_missing(rows)
        submitted=intake['status']=='submitted'
        return {'documents':rows,'status':intake['status'],'required_missing':missing,'upload_available':not submitted and d.upload_configured(),
                'message':('Solicitud recibida. Tus datos y archivos quedaron guardados. Hay documentos pendientes; el equipo podrá revisarlos y dar seguimiento contigo. Esto no implica aprobación.' if submitted and missing
                           else 'Solicitud recibida. Tus datos y documentos quedaron guardados y pendientes de revisión. Esto no implica aprobación.' if submitted
                           else 'Los documentos obligatorios están recibidos. Ya puedes finalizar tu solicitud.' if not missing
                           else 'Puedes finalizar tu solicitud aunque falten documentos. Los pendientes quedarán registrados para el seguimiento del equipo.')}

    @app.post('/intakes/{intake_id}/finalize')
    def finalize(intake_id:UUID,request:Request):
        user_id=owner(request)
        with pool.connection() as conn:
            with conn.transaction():
                intake=intake_for_owner(conn,intake_id,user_id)
                people,rows=scope(conn,intake_id,user_id,editable=False)
                if intake['status']=='submitted':return {'ok':True,'status':'submitted'}
                if conn.execute("SELECT 1 FROM document_uploads WHERE intake_id=%s AND status='processing' AND updated_at>now()-interval '90 seconds' LIMIT 1",(intake_id,)).fetchone():
                    raise HTTPException(409,'Hay un archivo que todavía se está guardando. Espera a que termine antes de finalizar.')
                states=conn.execute("SELECT participant_id,document_code,status,upload_id FROM document_states WHERE intake_id=%s",(intake_id,)).fetchall()
                groups=d.grouped_requirements(rows,people,states)
                missing=d.required_missing(groups)
                pending=[{'code':group['code'],'applicable':group['applicable'],
                          'participants':[m['participant_id'] for m in group['members']]}
                         for group in groups if group['required'] and group['applicable'] is not False
                         and (group['applicable'] is not True or group['status']!='received')]
                # Persist shared coverage for downstream review and audit.
                for group in groups:
                    if group['status']=='received' and group['upload_id']:
                        for member in group['members']:
                            if member['status']!='received':
                                assign(conn,intake_id,member['participant_id'],member['code'],'received',group['upload_id'])
                                event(conn,intake_id,user_id,member['participant_id'],member['code'],'reuse',{'upload_id':str(group['upload_id']),'automatic':True})
                applicant=next(p for p in people.values() if p['role']=='solicitante')
                conn.execute("UPDATE intakes SET status='submitted',updated_at=now() WHERE id=%s AND owner_id=%s",(intake_id,user_id))
                event(conn,intake_id,user_id,applicant['id'],None,'finalize',{'recognition_started':False,
                      'required_missing':missing,'pending_documents':pending})
        return {'ok':True,'status':'submitted'}

    @app.post('/intakes/{intake_id}/documents/dependency')
    def dependency(intake_id:UUID,body:DependencyInput,request:Request):
        user_id=owner(request)
        value=body.value.strip()
        if not value:raise HTTPException(422,'Indica el dato solicitado')
        if body.field=='estado_civil' and value.casefold() not in {'casado','soltero','divorciado','viudo','unión libre'}:
            raise HTTPException(422,'Selecciona una opción de estado civil; si no lo sabes, deja el documento pendiente')
        with pool.connection() as conn:
            with conn.transaction():
                _,rows=scope(conn,intake_id,user_id)
                if not any(r['participant_id']==str(body.participant_id) and r['dependency_field']==body.field for r in rows):
                    raise HTTPException(404,'El dato no corresponde a esta solicitud')
                conn.execute('INSERT INTO document_dependencies(intake_id,participant_id,field_code,value) VALUES(%s,%s,%s,%s) ON CONFLICT(participant_id,field_code) DO UPDATE SET value=EXCLUDED.value,updated_at=now()',
                             (intake_id,body.participant_id,body.field,value))
                event(conn,intake_id,user_id,body.participant_id,None,'dependency_answer',{'field':body.field,'value':value})
        return {'ok':True}

    @app.post('/intakes/{intake_id}/documents/{participant_id}/{code}/defer')
    def defer(intake_id:UUID,participant_id:UUID,code:str,request:Request):
        user_id=owner(request)
        with pool.connection() as conn:
            with conn.transaction():
                people,rows=scope(conn,intake_id,user_id)
                requirement(rows,participant_id,code,applicable=False)
                states=conn.execute('SELECT participant_id,document_code,status,upload_id FROM document_states WHERE intake_id=%s',(intake_id,)).fetchall()
                group=next(g for g in d.grouped_requirements(rows,people,states) if any(m['participant_id']==str(participant_id) and m['code']==code for m in g['members']))
                if group['status']=='received':
                    raise HTTPException(409,'Ese documento ya está recibido')
                for member in group['members']:
                    assign(conn,intake_id,member['participant_id'],member['code'],'deferred')
                    event(conn,intake_id,user_id,member['participant_id'],member['code'],'defer')
        return {'ok':True}

    @app.post('/intakes/{intake_id}/documents/{participant_id}/{code}/reuse')
    def reuse(intake_id:UUID,participant_id:UUID,code:str,body:ReuseDocumentInput,request:Request):
        user_id=owner(request)
        with pool.connection() as conn:
            with conn.transaction():
                people,rows=scope(conn,intake_id,user_id)
                requirement(rows,participant_id,code)
                requirement(rows,body.source_participant_id,code)
                target=people[str(participant_id)]
                source=people[str(body.source_participant_id)]
                if participant_id==body.source_participant_id or not target.get('rfc') or target['rfc']!=source.get('rfc'):
                    raise HTTPException(409,'Solo puedes reutilizar documentos del mismo RFC dentro de esta solicitud')
                saved=conn.execute("SELECT s.upload_id FROM document_states s JOIN document_uploads u ON u.id=s.upload_id AND u.intake_id=s.intake_id WHERE s.intake_id=%s AND s.participant_id=%s AND s.document_code=%s AND s.status='received' AND u.status='received'",(intake_id,body.source_participant_id,code)).fetchone()
                if not saved:raise HTTPException(409,'El documento de origen todavía no está recibido')
                assign(conn,intake_id,participant_id,code,'received',saved['upload_id'])
                event(conn,intake_id,user_id,participant_id,code,'reuse',{'source_participant_id':str(body.source_participant_id),'upload_id':str(saved['upload_id'])})
        return {'ok':True}

    @app.put('/intakes/{intake_id}/documents/{participant_id}/{code}/uploads/{upload_id}')
    async def upload(intake_id:UUID,participant_id:UUID,code:str,upload_id:UUID,request:Request):
        user_id=owner(request)
        with pool.connection() as conn:
            _,rows=scope(conn,intake_id,user_id)
            requirement(rows,participant_id,code)
        if not d.upload_configured():
            raise HTTPException(503,'La carga de archivos estará disponible en breve. Puedes conservar tus pendientes.')
        chunks=[]
        size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>d.MAX_BYTES:raise HTTPException(413,'El archivo pesa más de 10 MB')
            chunks.append(chunk)
        content=b''.join(chunks)
        try:mime,extension=d.inspect_file(content)
        except ValueError as exc:raise HTTPException(422,str(exc))
        digest=hashlib.sha256(content).hexdigest()
        with pool.connection() as conn:
            with conn.transaction():
                people,rows=scope(conn,intake_id,user_id)
                requirement(rows,participant_id,code)
                rfc=people[str(participant_id)]['rfc']
                previous=conn.execute('SELECT * FROM document_uploads WHERE id=%s',(upload_id,)).fetchone()
                if previous:
                    if (previous['intake_id']!=intake_id or previous['participant_id']!=participant_id or previous['document_code']!=code or previous['sha256']!=digest):
                        raise HTTPException(409,'La petición ya corresponde a otro archivo')
                    if previous['status']=='received':return {'ok':True,'status':'received'}
                    if previous['status']=='processing' and time.time()-previous['updated_at'].timestamp()<90:
                        raise HTTPException(409,'El archivo se está guardando; espera un momento')
                usage=conn.execute("SELECT count(*) AS n FROM document_uploads u JOIN intakes i ON i.id=u.intake_id WHERE i.owner_id=%s AND u.created_at>now()-interval '1 hour'",(user_id,)).fetchone()['n']
                if usage>=50:raise HTTPException(429,'Has enviado muchos archivos; intenta más tarde')
                conn.execute("INSERT INTO document_uploads(id,intake_id,participant_id,document_code,sha256,byte_size,mime_type,status) VALUES(%s,%s,%s,%s,%s,%s,%s,'processing') ON CONFLICT(id) DO UPDATE SET status='processing',updated_at=now()",(upload_id,intake_id,participant_id,code,digest,len(content),mime))
        try:
            storage_id=await run_in_threadpool(d.store_in_sharepoint,content,{'upload_id':str(upload_id),'intake_id':str(intake_id),
                'participant_id':str(participant_id),'rfc':rfc,'document_code':code,'mime_type':mime,
                'file_name':f'{rfc}-{code.replace("_", "-")}-por-revisar-{upload_id}.{extension}'})
            with pool.connection() as conn:
                with conn.transaction():
                    intake_for_owner(conn,intake_id,user_id,editable=True)
                    conn.execute("UPDATE document_uploads SET status='received',storage_id=%s,updated_at=now() WHERE id=%s AND intake_id=%s",(storage_id,upload_id,intake_id))
                    people,rows=scope(conn,intake_id,user_id)
                    requirement(rows,participant_id,code)
                    group=next(g for g in d.grouped_requirements(rows,people) if any(m['participant_id']==str(participant_id) and m['code']==code for m in g['members']))
                    for member in group['members']:
                        assign(conn,intake_id,member['participant_id'],member['code'],'received',upload_id)
                        if member['participant_id']!=str(participant_id):
                            event(conn,intake_id,user_id,member['participant_id'],member['code'],'reuse',{'source_participant_id':str(participant_id),'upload_id':str(upload_id),'automatic':True})
                    event(conn,intake_id,user_id,participant_id,code,'upload',{'upload_id':str(upload_id),'sha256':digest})
        except (d.DocumentUnavailable,HTTPException) as exc:
            with pool.connection() as conn:
                conn.execute("UPDATE document_uploads SET status='failed',updated_at=now() WHERE id=%s AND intake_id=%s",(upload_id,intake_id))
            if isinstance(exc,HTTPException):raise
            logger.warning('Document upload failed: %s',exc)
            raise HTTPException(503,'No pudimos guardar el archivo. Tu captura sigue guardada; vuelve a intentar.')
        return {'ok':True,'status':'received'}
