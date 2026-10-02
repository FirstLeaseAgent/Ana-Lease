# Accionistas en la solicitud de empresa

Las solicitudes PM nuevas capturan solicitante (incluido el contacto), representantes, accionistas y avales, en ese orden. Las solicitudes existentes conservan el flujo anterior; agregar un accionista activa las nuevas listas en esa solicitud. No se consultan SAT ni Buró por agregar únicamente el rol accionista.

Cada accionista pertenece al solicitante PM de su propio expediente mediante `participants.company_id`, con una clave foránea compuesta que impide vincular empresas de otro expediente. Una misma persona puede participar en distintas empresas sin mezclar el porcentaje de participación.

## Campos y validación

- RFC protegido, solicitado primero para determinar PF o PM.
- PF: nombre completo, CURP y porcentaje de participación.
- PM: razón social y porcentaje de participación. No se pregunta CURP de una empresa.
- Participación mayor al 10% y hasta 100%, según el formato aportado. La suma capturada no puede superar 100%. No se exige sumar 100%, porque se capturan únicamente los principales accionistas.
- Hasta tres registros, como en el formato PM. No se admite el mismo RFC dos veces como accionista de la misma empresa.
- La CURP se valida por formato de 18 caracteres. Esta comprobación no verifica existencia ni coincidencia con el RFC en registros oficiales.

Los campos base aparecen en el panel y en su plantilla Excel. Los catálogos antiguos adquieren esos campos como propuesta al abrir el panel, sin reescribir versiones históricas. Una solicitud antigua que recibe el nuevo rol usa los campos base de accionista si su snapshot no incluía el rol. El porcentaje y la identidad básica de ambos tipos son obligatorios para este rol; otras preguntas pueden agregarse desde el catálogo.

## Conversación

1. Completar empresa y contacto, después representante.
2. Escribir `accionista` o `accionista RFC`. Se pregunta un campo pendiente por turno.
3. Para identidad PF existente: `Agrega un accionista; es el mismo representante`. Copia los datos comunes admitidos que estén disponibles, incluido RFC y CURP, y pregunta lo faltante. No copia cargo, ocupación ni porcentaje.
4. Al terminar la lista: `listo accionistas`. Si no hay accionistas con participación superior al umbral: `no hay accionistas con más del 10%`.
5. Agregar avales; cerrar con `listo avales` o `sin aval` para pasar a documentos.

Los cierres se validan contra los datos guardados y se registran en `capture_turns` junto con la versión de captura. `ok` no cierra listas. No se admite cerrar si faltan campos de la fase actual o anterior. Agregar otro participante reabre su lista.

El ciclo del nuevo rol y las respuestas a sus preguntas se procesan en el servidor con evidencia del mensaje actual. Funcionan con el webhook y esquema n8n existentes, sin importar ni reemplazar el workflow en producción. La IA sigue interpretando los demás roles. Dudas o referencias ambiguas del accionista reciben una aclaración, sin almacenar la frase como un dato.

## Documentos y reanudación

`Accionista` ya está disponible en el catálogo documental y ahora se aplica a participantes reales del nuevo rol. Se conserva la configuración vigente: no se inventan requisitos documentales. Los documentos del accionista usan su tipo PF/PM. La interfaz sin IA también puede crear el rol y cerrar las listas mediante endpoints autenticados, sin llamar al modelo.

Verificación automatizada: pruebas de PF y PM, identidad compartida, cierre explícito, reanudación, suma de porcentajes, duplicados, límite, vínculo a la empresa, campos del catálogo, documentos, atomicidad del turno y compatibilidad con n8n.
