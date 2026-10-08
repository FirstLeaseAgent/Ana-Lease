"""Private, read-only intake follow-up for the existing administrators."""
import os
from pathlib import Path
from uuid import UUID
from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from . import documents as d


def install(app,pool,owner,conversation_people,notify=None):
    def administrator(conn,request):
        uid=owner(request)
        row=conn.execute('SELECT email FROM users WHERE id=%s',(uid,)).fetchone()
        allowed={v.strip().casefold() for v in os.environ.get('CAPTURE_CONFIG_ADMIN_EMAILS','').split(',') if v.strip()}
        if not row or row['email'].casefold() not in allowed:
            raise HTTPException(403,'Este usuario no tiene permiso para consultar solicitudes')
        return uid

    def documents(conn,intake_id,people):
        deps=conn.execute('SELECT participant_id,field_code,value FROM document_dependencies WHERE intake_id=%s',(intake_id,)).fetchall()
        states=conn.execute('SELECT participant_id,document_code,status,upload_id FROM document_states WHERE intake_id=%s',(intake_id,)).fetchall()
        return d.grouped_requirements(d.requirements(people,{(str(r['participant_id']),r['field_code']):r['value'] for r in deps}),people,states)

    @app.get('/seguimiento',response_class=HTMLResponse)
    def page():
        return Path(__file__).resolve().parent.parent.joinpath('static/followup.html').read_text()

    @app.get('/followup.js')
    def script():
        return Response(Path(__file__).resolve().parent.parent.joinpath('static/followup.js').read_text(),media_type='application/javascript')

    @app.get('/admin/intakes')
    def listing(request:Request,offset:int=0):
        if offset<0:raise HTTPException(422,'Página inválida')
        with pool.connection() as conn:
            administrator(conn,request)
            rows=conn.execute('SELECT i.id,i.rfc,i.status,i.updated_at,u.email AS owner_email,n.status AS notification_status FROM intakes i JOIN users u ON u.id=i.owner_id LEFT JOIN submission_notifications n ON n.intake_id=i.id ORDER BY i.updated_at DESC,i.id LIMIT 51 OFFSET %s',(offset,)).fetchall()
            result=[]
            for row in rows[:50]:
                people=conversation_people(conn,row['id'])
                applicant=next((p for p in people.values() if p['role']=='solicitante'),{})
                answers=applicant.get('answers',{})
                docs=documents(conn,row['id'],people)
                result.append({**row,'name':answers.get('razon_social') or answers.get('nombre') or 'Sin nombre capturado','required_missing':d.required_missing(docs)})
        return {'intakes':result,'has_more':len(rows)>50}

    @app.get('/admin/intakes/{intake_id}')
    def detail(intake_id:UUID,request:Request):
        with pool.connection() as conn:
            administrator(conn,request)
            intake=conn.execute('SELECT i.id,i.rfc,i.status,i.updated_at,u.email AS owner_email FROM intakes i JOIN users u ON u.id=i.owner_id WHERE i.id=%s',(intake_id,)).fetchone()
            if not intake:raise HTTPException(404,'Solicitud no encontrada')
            people=conversation_people(conn,intake_id)
            docs=documents(conn,intake_id,people)
            turns=conn.execute("SELECT user_message,response_json,created_at FROM capture_turns WHERE intake_id=%s AND status='complete' ORDER BY created_at,request_id",(intake_id,)).fetchall()
            events=conn.execute('SELECT action,document_code,details,created_at FROM document_events WHERE intake_id=%s ORDER BY created_at,id',(intake_id,)).fetchall()
        return {'intake':intake,'participants':[{'id':p['id'],'role':p['role'],'subject_type':p['subject_type'],'rfc':p['rfc'],'answers':p['answers']} for p in people.values()],
                'documents':docs,'required_missing':d.required_missing(docs),'conversation':turns,'events':events}

    @app.post('/admin/intakes/{intake_id}/notification/retry')
    def retry(intake_id:UUID,request:Request):
        with pool.connection() as conn:
            administrator(conn,request)
            if not conn.execute('SELECT intake_id FROM submission_notifications WHERE intake_id=%s',(intake_id,)).fetchone():
                raise HTTPException(404,'No existe aviso para esta solicitud')
        if notify:notify(intake_id)
        return {'ok':True}
