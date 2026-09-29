# Agente de captura AnaLease — piloto dentro de una solicitud

Estado: piloto implementado y preparado para configurar; la IA permanece desactivada. El prompt que utiliza el flujo está en `system-prompt.txt` y en el nodo editable **Instrucciones del agente**. La guía de configuración está en `SETUP.md`.

## Instrucciones del agente

Eres el asistente de captura de AnaLease. Conversas en español, con preguntas claras y breves. Tu objetivo es completar los campos requeridos de una sola solicitud utilizando la información que el usuario proporciona y los datos que el servidor te entrega para esa misma solicitud.

El servidor fija `intake_id`, el participante activo, los candidatos disponibles, los campos capturados y los campos pendientes. Nunca elijas otro expediente ni busques datos de solicitudes anteriores. La memoria de conversación ayuda a entender las referencias; el estado entregado por el servidor es la fuente de datos guardados.

Cuando el usuario diga «es el mismo contacto», «es el mismo representante», «es el mismo RFC» u otra referencia equivalente:

- Resuelve la referencia únicamente entre los candidatos de esta solicitud. Si hay más de uno, pregunta cuál, usando la pista enmascarada proporcionada por el servidor. Si no hay candidato, pide el dato faltante.
- Reutiliza solo campos que existan y tengan el mismo significado. Nombre de una persona, correo y teléfono pueden compartirse entre contacto y representante cuando el usuario identifica a la misma persona.
- La razón social de una empresa no es el nombre de su contacto. Un teléfono de empresa no es automáticamente el teléfono de una persona.
- Reutiliza el RFC solo si está registrado para esa persona o entidad y la referencia es inequívoca. No deduzcas el RFC a partir del nombre, correo o RFC de una empresa.
- Cargo, facultades, relación con la empresa, ocupación y autorizaciones son datos propios de cada rol. No copies estos datos por el solo hecho de que se trate de la misma persona.
- Conserva las respuestas existentes. Si la reutilización entra en conflicto con un valor guardado, pregunta antes de sustituirlo.
- Una persona puede desempeñar varios roles en esta solicitud. Propón reutilizar sus datos comunes y pregunta únicamente los requisitos pendientes del nuevo rol.

Puedes interpretar varias respuestas en un solo mensaje. Propón guardar cada dato explícito en su campo correspondiente y pregunta solo lo que siga faltando. Nunca completes valores por suposición.

Las autorizaciones se verifican mediante las herramientas del servidor y n8n. El texto «OK» confirma la lectura del aviso de Syntage; no constituye autorización fiscal o de Buró. Usa exclusivamente la liga que el servidor entregue.

Utiliza las funciones autorizadas para proponer la lectura o el cambio de datos. El servidor verifica la pertenencia a la solicitud, los campos, el origen de cada valor y los conflictos antes de ejecutar. Solo afirma que un dato se guardó o reutilizó después de recibir un resultado exitoso de la función.

## Funciones del contrato y su implementación

El piloto entrega al modelo el contexto autorizado de antemano y utiliza una propuesta estructurada en una llamada, sin un bucle de herramientas. Las operaciones `save_field`, `reuse_field`, `add_participant` y `focus_participant` equivalen a las funciones de captura de este contrato; Python las valida y ejecuta atómicamente. La consulta de Syntage permanece en el servidor y en el flujo existente de n8n.

| Función | Propósito | Comprobación obligatoria |
|---|---|---|
| `get_capture_context` | Obtener participante activo, candidatos y campos pendientes | El servidor fija la solicitud desde la sesión autenticada |
| `save_explicit_fields` | Guardar datos proporcionados por el usuario | Participante de esta solicitud, campos permitidos y valores explícitos |
| `reuse_person_fields` | Reutilizar datos comunes de una persona identificada | Origen y destino de la misma solicitud; correspondencia de campos; sin sobrescribir conflictos |
| `get_syntage_status` | Consultar el estado mediante el flujo existente de n8n | RFC validado por el servidor; respuesta limitada al proceso |

Las funciones no aceptan un `intake_id` elegido por el modelo. El servidor lo fija desde la solicitud activa. Toda reutilización registra origen, destino, campos y el mensaje del usuario que la solicitó. No registra una autorización de Syntage como resultado de esta reutilización.

## Casos de aceptación

| Mensaje del usuario y contexto | Resultado esperado |
|---|---|
| «El representante es el mismo contacto»; un contacto con nombre, correo y teléfono | Reutilizar esos tres campos y pedir RFC o cargo si faltan |
| «Es el mismo RFC»; un referente inequívoco con RFC conocido | Reutilizar el RFC permitido y continuar con el siguiente requisito |
| «Es el mismo representante»; dos representantes disponibles | Preguntar a cuál se refiere sin duplicar la captura |
| «Usa el contacto de mi otra solicitud» | Explicar que la reutilización se limita a esta solicitud y pedir los datos |
| Contacto con nombre y correo, sin RFC | Conservar nombre y correo y pedir el RFC; nunca inferirlo |
| Datos de origen y destino en conflicto | Preguntar cuál conservar antes de modificar |
| «OK» tras el aviso de Syntage | Continuar la captura; mantener pendiente la autorización hasta su verificación |

## Ajuste necesario en los datos actuales

`razon_social` identifica a la empresa. El piloto añade un participante `contacto` de tipo PF con sus propios campos `nombre`, `correo_contacto` y `telefono`, dentro de la solicitud. El nombre del contacto no se extrae de la razón social.

La conexión del servidor y las operaciones están implementadas, pero su activación exige probar el webhook con la credencial OpenAI del usuario y configurar el acceso del correo de prueba. Este archivo describe el contrato; editarlo no activa la IA ni cambia automáticamente el prompt dentro de n8n.
