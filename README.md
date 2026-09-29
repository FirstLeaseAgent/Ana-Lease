# AnaLease · captura inicial aislada

Primer tramo funcional para desplegar en **recursos nuevos**. No importa datos de los servicios actuales, no llama al webhook antiguo de n8n y no consulta Syntage ni SharePoint. Contiene el flujo n8n **nuevo y exclusivo** que ya se configuró y probó para entregar códigos por correo. El portal no está desplegado.

## Alcance actual

- Correo con código de seis dígitos, un solo uso, 10 minutos y cinco intentos. Máximo diez solicitudes por hora por correo y cien por dirección de origen, con intervalo mínimo de un minuto por correo. Antes de abrirlo a clientes se debe revisar la identificación de IP detrás del proxy de Render y el límite con tráfico real.
- Cookie de sesión de 14 días, HttpOnly, Secure y SameSite=Lax. `PUBLIC_ORIGIN` se compara con Origin para escrituras.
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

## Configuración de Render pendiente de publicación

`render.yaml` propone exclusivamente `analease-captura-web` y `analease-captura-db` en Oregon. Se debe revisar el costo de ambos planes, el nombre del dominio y el flujo n8n nuevo antes de sincronizar. El valor `PUBLIC_ORIGIN` debe ser el origen HTTPS exacto de la nueva URL. Se desactivó el despliegue automático. **Nunca sincronizar este Blueprint con un recurso existente ni apuntar `DATABASE_URL` a `cartera-historica-db`.**

Los planes propuestos cuestan aproximadamente **USD 13/mes** por servicio web y base (USD 7 + USD 6), sin contar almacenamiento superior al incluido, transferencia adicional ni otros cargos del workspace. Consultar el [precio vigente de Render](https://render.com/pricing) antes de aplicar. La app comprueba conexión a la base en `/health`, por lo que el despliegue solo debe marcarse sano con la base disponible.

## Próximas etapas

1. Captura y almacenamiento privado de documentos; validación de formato y análisis de archivos.
2. Reglas de requisitos completas a partir de las tablas actuales de campos y documentos, con bucles por RFC y estados de revisión. El conjunto de campos de esta entrega es deliberadamente pequeño.
3. Integraciones de Syntage y SharePoint desde el servidor, sin datos internos en respuestas públicas.
4. Conciliación interna entre capturas de distintos correos, expediente y revisión humana. Expiración de capturas inconclusas.

El código se conserva en una rama nueva de `FirstLeaseAgent/Ana-Lease`, sin reemplazar la página HTML de prueba de `main`. El repositorio actualmente es público; no se deben agregar datos de clientes ni secretos. El despliegue solo se hará conectando la rama nueva con recursos nuevos en Render.
