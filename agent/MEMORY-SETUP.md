# Memoria por solicitud

El portal utiliza el historial existente de `capture_turns` en PostgreSQL de Render. No requiere otra base, tablas vectoriales, credenciales nuevas ni reemplazar el flujo activo de n8n.

Cada llamada recupera hasta 12 turnos terminados, en orden de guardado. Los últimos tres y, cuando corresponde, la pregunta visible se envían en `history` (máximo cuatro entradas). Los anteriores se envían en `context.conversation_memory.earlier_turns`. Se conservan los mensajes reales y las respuestas que mostró el servidor; los turnos fallidos o en proceso se excluyen. Todo el historial continúa guardado aunque solo se entregue una ventana reciente al modelo.

Las relaciones se reconstruyen desde las acciones auditadas de toda la solicitud, usando la última acción por participante y campo. Un guardado directo o un deshacer sustituye la referencia anterior. También se comprueba que origen y destino pertenezcan a la solicitud y que los valores actuales coincidan. La memoria entregada al modelo contiene IDs, roles y códigos de campo; no reproduce los valores de las respuestas guardadas. El historial reciente sí conserva los datos que el usuario escribió en sus mensajes, como ocurría antes.

`shared_field` significa que un campo concreto se reutilizó. Compartir correo o teléfono no confirma identidad. `same_person` se registra únicamente tras una declaración explícita reconocida de que el representante es el contacto y una reutilización validada. Se guarda junto con las respuestas y la respuesta mostrada, dentro de la misma transacción. Se invalida para el contexto si las identidades o los datos actuales entran en conflicto. Una referencia breve como «igual» puede utilizar esta identidad confirmada para responder al campo común pendiente. No permite copiar cargo ni inferir un RFC que no está registrado.

El estado actual, el catálogo de la solicitud y la pregunta visible prevalecen sobre el historial. Las aclaraciones del modelo se conservan, pero no se permite saltar a otra pregunta del catálogo sin una acción aceptada. Los controles de propietario, idempotencia y versión de captura continúan aplicándose antes de guardar respuestas y memoria.

El contexto incluye las instrucciones para interpretar esta memoria. El contrato y el esquema del webhook permanecen iguales; el flujo existente transmite `context` completo a OpenAI. No hay que reimportarlo ni modificar sus credenciales. El modelo conserva sus instrucciones actuales y `store:false`.

## Comprobación

Completar un contacto, agregar un representante y responder «Los datos del representante son los mismos que el contacto». Deben reutilizarse los campos comunes disponibles. Capturar el RFC y cargo pendientes, recargar y verificar la reanudación. Una referencia a otro expediente, datos en conflicto o un mensaje de incertidumbre debe mantenerse como aclaración y no avanzar por suposición.

Pruebas: `python -m unittest discover -s tests` y `node tests/test_n8n_memory.js`.
