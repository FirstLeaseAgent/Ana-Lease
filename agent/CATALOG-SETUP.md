# Administración de catálogos AnaLease

El panel `/admin` usa la misma sesión por código OTP del portal. Solo los correos verificados enumerados en `CAPTURE_CONFIG_ADMIN_EMAILS` (separados por comas) pueden leer o modificar sus catálogos. Sin esa variable se deniega el acceso. No cambia la lista de usuarios autorizados para capturar solicitudes.

Permite editar preguntas por rol y PF/PM, activarlas o desactivarlas, cambiar texto, orden, tipo, opciones y origen de reutilización dentro del mismo participante. Cada pregunta activa se solicita; en este piloto todas las preguntas activas forman parte de los datos necesarios para avanzar. RFC, determinación PF/PM, autenticación, consentimiento, número máximo de avales y necesidad del representante de una PM siguen siendo reglas protegidas del motor.

Los documentos permiten nombre, código, rol, PF/PM, orden, obligatoriedad y condición por campo/valor. Contacto no tiene requisitos documentales; Accionista conserva plantillas para una futura etapa de ese rol. Recibir un documento no valida su contenido. Reconocimiento automático sigue pendiente.

El Excel actual se descarga desde el panel y se importa como borrador con las hojas `Preguntas` y `Documentos`. Se conservan los encabezados de la plantilla; no se admiten macros ni fórmulas. Opciones de listas se separan con `|`; booleanos usan TRUE/FALSE o Sí/No. Límite: 1 MB comprimido. La vista previa valida códigos, duplicados, tipos y reglas de reutilización antes de publicar.

Cada publicación agrega una versión; recuperar una anterior crea un borrador que debe validarse y publicarse nuevamente. Una publicación desactualizada se rechaza para no sobrescribir cambios de otro administrador. Cada solicitud conserva su snapshot del catálogo. Los expedientes existentes sin snapshot se congelan con el catálogo anterior al publicar por primera vez. Los nuevos usan la versión vigente al crear la solicitud; agregar roles conserva la versión de la solicitud. No se renombran archivos ni se alteran respuestas de expedientes existentes.

## Ajuste único en n8n

En el flujo de conversación, sustituir solo el JavaScript de `Preparar contexto y prompt` por el contenido de `agent/n8n-prepare-catalog.js`. Ese esquema permite códigos de campos dinámicos; el servidor valida cada acción contra el catálogo de la solicitud. No cambiar credenciales, URLs ni el flujo de documentos. Guardar y publicar.

En el nodo de instrucciones puede añadirse el apartado `CATÁLOGO CONFIGURABLE` de `agent/system-prompt.txt`; el contexto ya entrega `capture_schema` y `current_question` con los campos y preguntas permitidos. Esto facilita las listas y los campos nuevos. Los valores de datos previos no se entregan al modelo y solo se reutilizan dentro de la misma solicitud.
