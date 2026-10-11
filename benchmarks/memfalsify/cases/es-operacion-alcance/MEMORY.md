# Memoria de operación de Rumbal

<!-- Datos sintéticos de evaluación: Rumbal es una empresa ficticia. -->

## Latencia y errores

- La latencia p99 de la búsqueda tiene que mantenerse dentro de un cuarto de segundo.
- El presupuesto p99 de facturación es un cuarto de segundo.
- La confirmación de un pedido puede tardar hasta 2 minutos.
- El presupuesto mensual de errores de la búsqueda es una décima parte del uno por ciento.
- La búsqueda puede fallar en el 1 por ciento de las peticiones cada mes.

## Almacenamiento

- Los índices de búsqueda en el nivel caliente ocupan medio tebibyte.
- El nivel caliente guarda 512 GiB de índices de búsqueda.
- Los índices de búsqueda ocupan 512 GB en el nivel caliente.
- El archivo de facturas ocupa 500 GiB.
- La retención caliente de la búsqueda es de 90 días.

## Reintentos, guardias y versiones

- No se debe reintentar una búsqueda que ya respondió con HTTP 200.
- El 2026-07-22 se aceptó la ADR-015 con una regla que prohíbe reintentar consultas de búsqueda después de un HTTP 200.
- El renderizado de facturas no se puede reintentar.
- Reintentar el renderizado de una factura está permitido únicamente cuando el primer intento contestó con HTTP 503.
- El turno de facturación no debe reconocer una alerta de búsqueda.
- El turno de búsqueda no debe reconocer una alerta de facturación.
- Cualquier persona del turno de búsqueda puede silenciar una alerta de búsqueda.
- Pasaron 105 días entre el debut en producción de la versión 2.3.1, el 2026-05-20, y la sustitución por la versión 2.4.0.
- Solo pasaron 30 días entre el debut en producción de la versión 2.3.1, el 2026-05-20, y la sustitución por la versión 2.4.0.
- La versión de producción es la 2.3.2.
