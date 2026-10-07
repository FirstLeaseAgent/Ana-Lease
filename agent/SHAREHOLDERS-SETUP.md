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

## Respuestas naturales en las transiciones

En la pregunta por accionistas, se acepta una identidad inequívoca junto con su porcentaje: `el principal con 99.6% de participación es el mismo que el contacto`, o `es el mismo contacto y tiene el 99.6%`. Se crea el rol, se reutilizan los campos comunes admitidos y se guarda el porcentaje en el mismo turno atómico. Si la memoria confirma contacto = representante, el RFC disponible en el representante se puede reutilizar desde esa relación. Se pide la CURP si falta; no se vuelve a pedir el porcentaje. Una referencia ambigua o un porcentaje inválido no guarda parcialmente el turno.

En las transiciones de accionistas y avales, `es el mismo contacto`, el nombre completo inequívoco de una persona registrada, un nombre nuevo o un RFC pueden iniciar el rol que la pregunta solicita. El contexto de la fase determina ese rol; estas entradas no permiten crear participantes en una fase distinta o cerrar datos pendientes. Los nombres nuevos no aportan automáticamente RFC ni datos de contacto.

`no hay más`, `son todos`, `eso es todo` y `ninguno` cierran exclusivamente la lista que se está preguntando, cuando sus registros están completos. Nunca saltan CURP, porcentaje, RFC, ocupación u otros requisitos pendientes. `ok` conserva su significado de reconocimiento y no cierra listas.

Las respuestas literales cortas a cargo y ocupación, como `Director`, se guardan para el campo preguntado sin consultar al modelo. No se copia cargo como ocupación: el usuario debe dar esa respuesta para el nuevo rol. Las dudas y referencias siguen sin almacenarse como títulos. Las preguntas muestran un rol breve; si hay varios participantes de ese rol, muestran su número, sin repetir información personal.

La actualización de n8n del 7 de octubre incorpora accionista y las intenciones de cierre al esquema y prompt del workflow. Las frases no resueltas por las rutas deterministas llegan al modelo, con la etapa, rol pendiente y catálogo autorizado en contexto. El servidor conserva las validaciones, las restricciones de cierre y el guardado atómico. Ver N8N-CONVERSATION-UPGRADE.md para las pruebas con respuestas reales del modelo.
