# Piloto de conversación dentro de una solicitud

El servidor y el flujo de n8n están preparados. La IA permanece desactivada hasta configurar y probar el webhook. El código utiliza propuestas estructuradas: n8n interpreta el mensaje y Python valida cada acción antes de guardarla.

## Configurar n8n

1. Importar `n8n/AnaLease-conversacion-IA.json` como flujo nuevo.
2. En **Entrada conversación**, seleccionar una credencial Header Auth exclusiva: nombre de encabezado `X-AnaLease-Token`. Guardar su valor de forma privada en n8n y posteriormente en la variable del servidor `N8N_CONVERSATION_WEBHOOK_TOKEN`.
3. En **Interpretar mensaje con OpenAI**, elegir la credencial OpenAI existente. El nodo HTTP utiliza autenticación de credencial predefinida `openAiApi`; ninguna API key está dentro del JSON.
4. En **Instrucciones del agente**, el campo `instructions` contiene el prompt editable y `model` contiene `gpt-5.4-mini`. Estos campos pueden ajustarse desde la interfaz de n8n. El modelo debe admitir Responses y salidas estructuradas.
5. Publicar el flujo y probar su webhook con `agent/test-context.json`, que contiene únicamente datos ficticios y metadatos, sin respuestas personales.
6. Resultado esperado: propuesta de agregar un representante y reutilizar nombre, correo y teléfono del contacto; el texto debe pedir el RFC, ya que ese dato no está disponible. Esta prueba del webhook no guarda nada en la base del portal.

El flujo no conserva entradas ni salidas en ejecuciones exitosas o fallidas y envía `store:false` a OpenAI. Esto no equivale a una política de retención cero del proveedor. No solicita consultas a Syntage por su cuenta; el servidor continúa usando el flujo existente al aceptar el RFC de un solicitante o aval.

## Activar solo para pruebas

Una vez comprobado el webhook, configurar las siguientes variables en el servicio del portal:

| Variable | Valor |
|---|---|
| `N8N_CONVERSATION_WEBHOOK_URL` | Production URL del nuevo webhook |
| `N8N_CONVERSATION_WEBHOOK_TOKEN` | Valor privado de la credencial Header Auth del nuevo flujo |
| `CAPTURE_AI_ENABLED` | `true` |
| `CAPTURE_AI_ALLOWED_EMAILS` | Correo de prueba verificado; lista separada por comas si hay más de uno |

El piloto exige ambas condiciones: habilitación y correo incluido en la lista. Si falta cualquiera, continúa la captura existente. Guardar variables y desplegar la rama preparada para aplicar la configuración.

## Prueba del portal

1. Entrar con el correo permitido y abrir la misma solicitud de prueba.
2. Completar al solicitante. El contacto es una persona separada, con nombre, correo y teléfono.
3. Escribir «Quiero agregar un representante; es el mismo contacto».
4. Debe reutilizar esos tres campos y preguntar solo RFC y cargo pendientes.
5. Recargar y volver a entrar: debe retomar al representante y mostrar su pista enmascarada, sin repetir respuestas completas.
6. Ante dos posibles referentes, debe pedir aclaración. Ante una referencia a otro expediente, debe solicitar los datos dentro de esta solicitud.

El servidor valida pertenencia a la solicitud, campos permitidos, respaldo en el mensaje, conflictos, RFC PF para representantes y duplicados de rol/RFC. Las propuestas se guardan atómicamente, con origen y destino de reutilización. Un `request_id` repetido devuelve la respuesta previamente guardada y evita repetir escrituras. Si cambió la captura en otra pantalla, devuelve un conflicto y conserva los datos.

El piloto permite agregar roles y completar campos vacíos. La sustitución de un dato ya capturado requiere un flujo posterior de confirmación; este piloto rechaza cambios en valores existentes. La IA real todavía debe evaluarse con la credencial del usuario: las pruebas locales validan el contrato y la aplicación, no la calidad de las respuestas de un modelo en producción.
