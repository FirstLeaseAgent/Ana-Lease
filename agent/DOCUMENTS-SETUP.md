# Documentos de AnaLease

El catálogo proporcionado por el usuario se configura privadamente en `ANALEASE_DOCUMENT_CATALOG` en el servicio del portal. No se incluye en el repositorio público. Sus filas utilizan `scope`, `role`, `code`, `label`, `order`, `required`, `dependency_field` y `dependency_value`. El representante es PF; la columna PM de sus plantillas corresponde a su rol en la empresa. Un aval usa las plantillas de su propio tipo PF/PM. Las dependencias desconocidas se preguntan en documentos. Opcionales y no aplicables no bloquean la recepción de los obligatorios.

La captura pasa automáticamente a documentos cuando todos los participantes tienen completos los campos del piloto. Una solicitud PM requiere al menos un representante. No se exige un aval extra: el usuario puede agregar otro participante desde documentos y volver a completar sus datos antes de continuar. El catálogo completo de campos sigue fuera de este piloto.

## Conectar la carga a SharePoint

1. Importar `n8n/AnaLease-documentos-SharePoint.json` como flujo nuevo.
2. En **Entrada documento**, crear o elegir Header Auth exclusiva con encabezado `X-AnaLease-Token`. No utilizar los tokens de correo, Syntage o conversación.
3. En **Guardar en SharePoint**, seleccionar la credencial SharePoint existente, sitio y una carpeta dedicada al piloto AnaLease. El flujo usa explícitamente la versión 1 del nodo, que admite la credencial `microsoftSharePointOAuth2Api`. No convertirlo a versión 2 sin configurar su credencial Graph correspondiente.
4. Guardar y publicar. El path de producción es `/webhook/analease-documents`.
5. En el servicio del portal, guardar `N8N_DOCUMENTS_WEBHOOK_URL` con esa URL de producción y `N8N_DOCUMENTS_WEBHOOK_TOKEN` con el token privado de este flujo. Guardar y desplegar. La carga permanece deshabilitada si falta la configuración; el checklist y los pendientes sí están disponibles.
6. Probar con un PDF ficticio. Confirmar que el archivo aparece en la carpeta seleccionada y que el portal lo muestra como **Recibido · pendiente de revisión**.

El límite es 10 MB por archivo y los formatos son PDF, JPG y PNG. El servidor comprueba propietario, participante, requisito, aplicabilidad, tamaño y firma de formato. No valida el contenido documental ni la vigencia mediante esa firma. n8n no guarda los cuerpos de ejecuciones; los archivos no se envían al agente OpenAI.

El archivo se guarda en SharePoint; PostgreSQL conserva únicamente sus metadatos, huella, estados y eventos. El portal envía el RFC guardado del participante al que pertenece el requisito; no toma el RFC del nombre del archivo ni de un campo enviado por el navegador. Los nombres siguen `RFC-tipo-declarado-por-revisar-ID-de-carga.ext`. El tipo es el declarado durante la captura y todavía no se reconoce el contenido. La revisión automática al finalizar la solicitud queda para una segunda etapa. En la carpeta dedicada cada carga nueva es un aporte independiente; un reintento utiliza el mismo nombre y no crea otro aporte. La integración al expediente maestro por RFC y sus subcarpetas sigue pendiente de conciliación. No se modifica ninguna carpeta maestra automáticamente.

La reutilización requiere un clic explícito, mismo código documental, mismo RFC y misma solicitud. La recepción no valida ni aprueba un archivo. Los requisitos diferidos continúan pendientes y se retoman al volver al portal. No se marca la solicitud enviada ni aprobada.
