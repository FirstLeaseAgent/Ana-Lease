"""OTP-authenticated, explicitly allowlisted catalogue administration."""
import os
from pathlib import Path
from fastapi import HTTPException,Request,Response
from fastapi.responses import HTMLResponse
from psycopg.types.json import Jsonb
from . import catalog

async def bounded_body(request,limit):
    pieces=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>limit:raise HTTPException(413,'El archivo o catálogo excede el tamaño permitido')
        pieces.append(chunk)
    return b''.join(pieces)

def install(app,pool,owner):
    def administrator(conn,request):
        uid=owner(request)
        row=conn.execute('SELECT email FROM users WHERE id=%s',(uid,)).fetchone()
        allowed={s.strip().casefold() for s in os.environ.get('CAPTURE_CONFIG_ADMIN_EMAILS','').split(',') if s.strip()}
        if not row or row['email'].casefold() not in allowed:raise HTTPException(403,'Este usuario no tiene acceso a la administración de catálogos')
        return uid
    def check(config):
        try:return catalog.validate(config)
        except ValueError as exc:raise HTTPException(422,str(exc))
    @app.get('/admin',response_class=HTMLResponse)
    def page():return Path(__file__).resolve().parent.parent.joinpath('static/admin.html').read_text()
    @app.get('/admin.js')
    def script():return Response(Path(__file__).resolve().parent.parent.joinpath('static/admin.js').read_text(),media_type='application/javascript')
    @app.get('/admin/catalog')
    def read(request:Request,response:Response):
        with pool.connection() as conn:
            administrator(conn,request);version,config=catalog.active(conn)
            versions=conn.execute('SELECT id,created_at FROM capture_catalogs ORDER BY id DESC LIMIT 20').fetchall()
        response.headers['Cache-Control']='no-store'
        return {'version':version,'config':config,'versions':versions}
    @app.get('/admin/catalog/versions/{version_id}')
    def version(version_id:int,request:Request,response:Response):
        with pool.connection() as conn:
            administrator(conn,request)
            row=conn.execute('SELECT id,body FROM capture_catalogs WHERE id=%s',(version_id,)).fetchone()
        if not row:raise HTTPException(404,'Versión no encontrada')
        response.headers['Cache-Control']='no-store';return {'config':row['body']}
    @app.get('/admin/catalog/template.xlsx')
    def template(request:Request):
        with pool.connection() as conn:administrator(conn,request);_,config=catalog.active(conn)
        return Response(catalog.workbook(config),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="AnaLease-catalogos.xlsx"','Cache-Control':'no-store'})
    @app.post('/admin/catalog/import')
    async def preview_excel(request:Request):
        with pool.connection() as conn:administrator(conn,request)
        content=await bounded_body(request,1024*1024)
        try:config=catalog.parse_workbook(content)
        except ValueError as exc:raise HTTPException(422,str(exc))
        return {'config':config,'preview':True}
    @app.post('/admin/catalog/preview')
    async def preview(request:Request):
        with pool.connection() as conn:administrator(conn,request)
        import json
        try:config=json.loads(await bounded_body(request,512*1024))
        except (ValueError,UnicodeError):raise HTTPException(422,'Catálogo inválido')
        checked=check(config)
        return {'config':checked,'questions':sum(r['enabled'] for r in checked['fields']),'documents':len(checked['documents']),'required_documents':sum(r['required'] for r in checked['documents'])}
    @app.post('/admin/catalog/publish')
    async def publish(request:Request):
        with pool.connection() as conn:uid=administrator(conn,request)
        import json
        try:body=json.loads(await bounded_body(request,512*1024));config=check(body['config']);expected=body['base_version']
        except (KeyError,ValueError,TypeError):raise HTTPException(422,'Publicación inválida')
        if type(expected) is not int or expected<0:raise HTTPException(422,'Versión inválida')
        with pool.connection() as conn:
            with conn.transaction():
                administrator(conn,request)
                conn.execute('SELECT pg_advisory_xact_lock(72496103)')
                current,_=catalog.active(conn)
                if expected!=current:raise HTTPException(409,'Otro administrador publicó cambios. Recarga el catálogo antes de publicar.')
                conn.execute('UPDATE intakes SET catalog_snapshot=%s WHERE catalog_snapshot IS NULL',(Jsonb(catalog.baseline()),))
                row=conn.execute('INSERT INTO capture_catalogs(actor_id,body) VALUES(%s,%s) RETURNING id',(uid,Jsonb(config))).fetchone()
        return {'ok':True,'version':row['id'],'message':'Versión publicada. Se aplicará a solicitudes nuevas; los expedientes existentes conservan su catálogo.'}
