# Respuestas en las transiciones de captura

## Correo compartido seguido de teléfono

Si el contacto responde `el mismo del solicitante` a su correo, esa copia queda auditada como `shared_field`. Cuando la siguiente respuesta al teléfono es `igual`, el servidor usa el origen de la última copia de canal guardada para ese contacto y copia únicamente el teléfono, si está disponible.

La regla requiere un origen único, vigente y dentro de la solicitud. No se usa el usuario de sesión como origen del teléfono. No se convierte la relación de canales compartidos en identidad. Una instrucción intermedia que cambie o niegue la referencia impide arrastrarla. Una respuesta ambigua sin referencia previa no autoriza copiar datos por suposición.

También se resuelven localmente referencias explícitas de correo o teléfono, como `el mismo del solicitante` y `el mismo del aval 2`. Si existen varios candidatos y falta el número, se pide aclaración sin guardar ni cambiar de participante.

## Alta del representante después de empresa y contacto

La pregunta del servidor pide quién actuará como representante, y permite responder con el nombre completo o `es el mismo contacto`. No basta responder `ok` para crear el rol ni dar por capturados sus datos.

- Un nombre completo que coincide inequívocamente con una identidad PF ya registrada reutiliza los campos comunes disponibles para el nuevo rol de representante.
- La coincidencia de entrada ignora mayúsculas, acentos y espacios repetidos. El valor copiado conserva la escritura guardada.
- Si varias identidades tienen el mismo nombre, se pide identificar el rol de origen.
- Si se responde con un nombre nuevo, se crea el representante y se guarda ese nombre. No se copian los canales del contacto por defecto.
- Un RFC PF recibido en esta fase permite iniciar el representante con ese RFC. No se deducen datos desde el RFC.
- El RFC y cargo siguen solicitándose si faltan. No se adivina el RFC a partir del nombre.

La inferencia del rol se limita a la fase en que la empresa PM y el contacto están capturados y todavía no hay representante. Un nombre no permite agregar un aval o accionista, ni abrir un representante durante otra pregunta. Cada operación mantiene evidencia literal, validación de campos, auditoría y el control de versión del turno.

Estos ajustes funcionan en el servidor con el workflow n8n vigente y no requieren cambios en la base de datos ni en solicitudes existentes.
