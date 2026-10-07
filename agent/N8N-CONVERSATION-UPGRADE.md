# Conversación n8n: actualización del 7 de octubre de 2026

Se actualizaron en el workflow activo `gUNOqxWTnechZuI0` únicamente los nodos Instrucciones del agente y Preparar contexto y prompt. La exportación final se comparó con el respaldo: conexiones, IDs de nodos, referencias de credenciales, método, ruta y autenticación del webhook permanecen iguales. El flujo está guardado y activo. Los datos fijados para las pruebas fueron retirados antes del guardado final.

## Contrato y contexto

El esquema estricto conserva `reply/actions` y versión 1. Añade el rol `accionista` y las acciones `finish_shareholders` y `finish_guarantors`. `field` ya no enumera un catálogo fijo: los campos autorizados provienen del snapshot de la solicitud; Render verifica ese catálogo, el tipo y la evidencia literal. CURP y porcentaje pueden capturarse juntos, además de los campos personalizados del catálogo.

El contexto del servidor añade `capture_stage` y `participant_transition`: rol pendiente, ID de la empresa, posibilidad de cierre y esquemas PF/PM del rol nuevo. La pregunta por el siguiente participante autoriza interpretar una respuesta natural para ese rol, sin exigir que el usuario escriba su nombre como comando. Mensajes no resueltos en las etapas de accionistas y avales llegan al modelo; también las respuestas extensas, dudas y referencias no resueltas del accionista. Las rutas deterministas se mantienen para respuestas claras ya verificadas.

Una declaración explícita de la misma persona se audita solo tras reutilizar datos compatibles dentro de la solicitud. Compartir correo o teléfono no crea identidad. No se copian cargo, ocupación ni porcentaje entre roles. El modelo solo puede copiar campos admitidos por el destino; el catálogo base de accionista PF admite nombre, CURP, porcentaje y RFC protegido, sin correo ni teléfono.

Los cierres del modelo requieren la etapa correcta, participantes completos y evidencia expresa de terminación. OK no cierra una lista. Un cierre que nombra accionistas no puede cerrar avales, ni al revés. El servidor calcula la siguiente pregunta después de aceptar las acciones, para que el texto de la IA no vuelva a solicitar un campo ya guardado.

## Verificación

137 pruebas Python y la verificación JavaScript del esquema y transmisión de memoria. Además se ejecutaron pruebas con el modelo real en el editor de n8n y datos ficticios, usando las credenciales existentes sin consultas SAT/Buró ni modificaciones de solicitudes reales:

- `El socio mayoritario es el mismo contacto; su participación es 99.6%`: cuatro acciones aceptadas (crear accionista, copiar nombre del contacto, copiar RFC del representante vinculado, guardar porcentaje). Siguiente pregunta del servidor: CURP.
- `Su CURP es AACD010101HDFRRL01 y tiene el 60%`: dos campos aceptados; el servidor pregunta por otro accionista y no repite CURP.
- `Con él terminamos`: cierre de accionistas aceptado; el servidor pasa a avales.

Las propuestas reales aceptadas se conservan como fixtures sintéticos en `tests/fixtures/n8n-natural-cases.json` y se vuelven a validar en la suite. Las pruebas con modelo son muestras, no una garantía de interpretar cualquier redacción. Las propuestas inválidas siguen sin guardarse.

El modelo se mantiene en gpt-5.4-mini, store=false y salida JSON estricta. No se añadieron agentes nativos, bases vectoriales, credenciales ni cambios de base de datos.
