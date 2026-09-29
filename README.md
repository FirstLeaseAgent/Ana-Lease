# AnaLease · captura inicial aislada

Primer tramo funcional desplegado en **recursos nuevos** de Render. No importa datos de los servicios actuales, no llama al webhook antiguo de n8n y todavía no consulta Syntage ni SharePoint. El flujo n8n **nuevo y exclusivo** entrega los códigos por correo.

## Alcance actual

- Correo con código de seis dígitos, un solo uso, 10 minutos y cinco intentos. Máximo diez solicitudes por hora por correo y cien por dirección de origen, con intervalo mínimo de un minuto por correo. Antes de abrirlo a clientes se debe revisar la identificación de IP detrás del proxy de Render y el límite con tráfico real.
- Cookie de sesión de 14 días, HttpOnly, Secure y SameSite=Lax. El origen se toma de `RENDER_EXTERNAL_URL` en Render (o `PUBLIC_ORIGIN` al ejecutar fuera de Render) y se compara con Origin para escrituras.
- Cada correo ve y edita sus propias capturas. Un segundo correo con el mismo RFC crea una captura independiente; el servidor no muestra información de otra cuenta ni datos maestros existentes.
- Solicitante persona física o moral; hasta tres avales PF/PM; representantes PF. Preguntas según rol y tipo; respuesta guardada después de cada turno.

## Para levantarlo localmente

1. Crear una base PostgreSQL **nueva** para AnaLease y copiar `.env.example` a un archivo de entorno privado. Configurar `DATABASE_URL`, `OTP_PEPPER`, `N8N_MAIL_WEBHOOK_URL`, `N8N_MAIL_WEBHOOK_TOKEN` y `PUBLIC_ORIGIN` con el origen HTTPS exacto del navegador. Nunca publicar secretos en Git.
2. `pip install -r requirements.txt`
3. `python -m app.migrate`
4. `uvicorn app.main:app --host 0.0.0.0 --port 8000`

La cookie Secure exige HTTPS; para pruebas de navegador se necesita un origen HTTPS de desarrollo. No se ha configurado ni usado un buzón o credencial real.

## Envío con n8n y la credencial de correo existente

El backend genera y guarda únicamente el hash del código. Hace un POST servidor a servidor al webhook nuevo, con `{ "email": "...", "code": "..." }` y un encabezado `X-AnaLease-Token`. n8n manda el mensaje con la credencial **Microsoft Outlook OAuth2** que ya esté funcionando y responde `{ "ok": true }` después del nodo de envío. El navegador no conoce el webhook, el token ni la credencial de correo. El envío aceptado tampoco garantiza la entrega final del mensaje.

1. El flujo de envío está activo y la prueba real de correo ya funcionó. Se incluye `n8n/AnaLease - envio de codigo.json` como referencia; no volver a importarlo sobre el flujo activo.
2. Poner el valor de la credencial *Header Auth* `X-AnaLease-Token` en `N8N_MAIL_WEBHOOK_TOKEN` **directamente en el nuevo servicio de Render**, nunca en el repositorio ni en este chat.
3. Confirmar en Settings que no se guardan ejecuciones exitosas, fallidas ni manuales con datos: el cuerpo contiene un código vigente.
4. La Production URL está fijada en `.env.example` y `render.yaml`: `https://flagent.app.n8n.cloud/webhook/analease-otp-send`. No insertar la URL ni el token en el frontend.

Si la credencial existente es del nodo SMTP «Send Email» con usuario y contraseña de Microsoft 365, revisar si realmente funciona: [n8n indica](https://docs.n8n.io/integrations/builtin/credentials/send-email/outlook/) que ese método dejó de ser válido para Microsoft 365. El nodo [Microsoft Outlook](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.microsoftoutlook) admite el envío con la credencial OAuth2 ya conectada. n8n gestiona la integración del proveedor; la aplicación en Render no llama a Microsoft Graph. Si la credencial existente corresponde a otro proveedor, cambiar solo el nodo de envío después de importarlo.

## Configuración de Render

`analease-captura-web` y `analease-captura-db` son recursos nuevos y aislados en Oregon. El portal está en `https://analease-captura-web.onrender.com`. En Render el origen se obtiene automáticamente de `RENDER_EXTERNAL_URL`; no se debe configurar `PUBLIC_ORIGIN` salvo que se use un dominio propio. Se desactivó el despliegue automático del servicio. **Nunca sincronizar este Blueprint con un recurso existente ni apuntar `DATABASE_URL` a `cartera-historica-db`.**

`.python-version` fija Python 3.13.5 porque la versión de `psycopg[binary,pool]` de esta entrega no tiene distribución binaria para la versión 3.14 elegida por defecto en servicios nuevos de Render.

Los planes propuestos cuestan aproximadamente **USD 13/mes** por servicio web y base (USD 7 + USD 6), sin contar almacenamiento superior al incluido, transferencia adicional ni otros cargos del workspace. Consultar el [precio vigente de Render](https://render.com/pricing) antes de aplicar. La app comprueba conexión a la base en `/health`, por lo que el despliegue solo debe marcarse sano con la base disponible.

## Próximas etapas

1. La rama consulta Syntage por cada RFC de solicitante y aval mediante n8n, antes de guardar el RFC. Falta configurar `N8N_SYNTAGE_WEBHOOK_TOKEN` en Render y desplegar manualmente para activarlo. No consultar al SAT para verificar existencia del RFC. El navegador recibe únicamente la liga de autorización cuando se requiere, nunca los estados internos, la presencia de una entidad ni datos obtenidos de Syntage.
2. Si hace falta consentimiento o una credencial utilizable, ofrecer la liga **genérica** de onboarding de Syntage correspondiente al tipo inferido del RFC: 13 caracteres para persona física y 12 para persona moral. Las ligas confirmadas son [persona moral](https://registro.syntage.com/8de4ef?reporteDeCredito=true&personType=legal) y [persona física](https://registro.syntage.com/8de4ef?reporteDeCredito=true&personType=physical). El valor `physicaly` de la primera liga compartida para PF era un error y se corrige a `physical`. No crear una liga por expediente ni depender del `onboardingUrl` de `POST /entities`. Comprobar por separado el estado SAT y la autorización de Buró; la presencia de `reporteDeCredito=true` en la URL no prueba por sí misma que estén vigentes. No recoger contraseñas CIEC ni material de e.firma en AnaLease.
3. Captura y almacenamiento privado de documentos; validación de formato y análisis de archivos.
4. Reglas de requisitos completas a partir de las tablas actuales de campos y documentos, con bucles por RFC y estados de revisión. El conjunto de campos de esta entrega es deliberadamente pequeño.
5. Integración posterior con SharePoint y conciliación interna entre capturas de distintos correos, expediente y revisión humana. Expiración de capturas inconclusas.

El código se conserva en la rama `portal-otp-render` de `FirstLeaseAgent/Ana-Lease`, sin reemplazar la página HTML de prueba de `main`. El repositorio actualmente es público; no se deben agregar datos de clientes ni secretos. Render despliega esta rama solo cuando se dispara manualmente.

## Flujo n8n: estado Syntage por RFC

Importar `n8n/AnaLease - estado Syntage por RFC.json` como **flujo nuevo**. El archivo `n8n/build_syntage_status.py` genera el JSON importable. No activar ni conectar el flujo al portal antes de comprobarlo con RFC controlados.

1. En el nodo **Entrada RFC**, elegir autenticación *Header Auth* y crear una credencial exclusiva para este webhook, por ejemplo con nombre `X-AnaLease-Token` y un valor aleatorio distinto del usado para el correo. Guardar el valor únicamente en n8n y, cuando se conecte el backend, en el servicio aislado de AnaLease en Render.
2. Confirmar que la variable de n8n `API_SYNTAGE_KEY` está disponible en los nodos HTTP. El flujo consulta `GET /entities?taxpayer.id=...`, `GET /credentials?rfc=...` y, si existe la entidad, `GET /entities/{entityId}/datasources/mx/buro-de-credito/authorizations`. No crea entidades ni dispara reportes o extracciones.
3. Llamar al webhook de prueba con `POST`, encabezado de autenticación y cuerpo `{"rfc":"RFC_CONTROLADO"}`. Comprobar cuatro casos: RFC sin entidad, credencial SAT válida, credencial pendiente/inválida y autorización de Buró vigente o vencida. Publicar el flujo solo después de comparar esos resultados con Syntage.
4. La URL de producción compartida es `https://flagent.app.n8n.cloud/webhook/analease-syntage-status`. Guardar el valor de la credencial *Header Auth* en `N8N_SYNTAGE_WEBHOOK_TOKEN` únicamente en el servicio `analease-captura-web` de Render, con el mismo nombre de encabezado que espera el adaptador: `X-AnaLease-Token`. No usar el token del flujo de correo ni pegar la credencial en el repositorio o el chat. El servicio tiene el despliegue automático desactivado: este cambio en la rama no modifica la aplicación en producción.
5. La respuesta interna es `{"ok":true,"person_type":"legal","registered":true,"sat":"valid","buro":"missing","next_action":"onboarding"}`. `sat` admite `valid`, `pending`, `invalid`, `missing`; `buro` admite `valid`, `missing`; `next_action` admite `continue`, `wait`, `onboarding`. Errores y resultados inconclusos usan `{"ok":false,"error":"syntage_unavailable|review_required|invalid_rfc"}`. El backend debe verificar `ok` y fallar cerrado; nunca enviar esta respuesta cruda al navegador.

Se compara el RFC exacto porque los filtros de Syntage admiten coincidencias parciales. `sat=valid` requiere al menos una credencial validada, incluso si hay otras credenciales históricas inválidas; `buro=valid` requiere RFC exacto, `valid=true` (o `isValid=true` en otro formato), ausencia de `deletedAt` y `authorizedUntil` posterior a la hora de consulta. Los nodos de evaluación admiten tanto `hydra:member` como una lista de elementos emitidos por n8n. Si una página incompleta impide concluir, el flujo responde `review_required`. La existencia de una entidad por sí sola no prueba ninguna de las dos autorizaciones.
