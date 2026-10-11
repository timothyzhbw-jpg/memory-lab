# ADR-015 Tiempos de espera y reintentos

Aceptada el 2026-07-22, sustituye a la ADR-008.

El tiempo de espera de la pasarela para las consultas de búsqueda es de 1500 ms.

El tiempo de espera de la pasarela para el renderizado de facturas es de 8 segundos.

No se debe reintentar una consulta de búsqueda que ya devolvió HTTP 200.

Un renderizado de factura se puede reintentar como máximo una vez, y solo si el primer intento devolvió HTTP 503.

Hasta el 2026-07-22, las consultas de búsqueda se reintentaban incluso después de un HTTP 200.
