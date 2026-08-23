# Manual de acceso privado al CRM — NetBird

Versión: 1 de agosto de 2026  
Administrador: Oscar Mendoza (`oscar@greendiamond.cl`)

## Qué se está usando

NetBird crea una red privada cifrada entre el servidor CRM y los equipos autorizados. No abre el CRM a Internet, no requiere MikroTik y no necesita redireccionar puertos en el router Entel. El plan contratado es **Free: USD 0, hasta 5 usuarios y 100 equipos**. Green Diamond utilizará cuatro usuarios: Oscar y tres ejecutivos.

La VPN identifica el equipo; el usuario y los permisos dentro del CRM siguen siendo independientes. Perder acceso a la VPN no borra datos del CRM.

## Datos que entregará Oscar a cada ejecutiva

- Invitación individual enviada por NetBird a su correo corporativo.
- URL privada del CRM: `http://IP_NETBIRD_DEL_SERVIDOR/crm/`.
- Usuario y contraseña personal del CRM. Nunca se comparte una cuenta.

No se envían Setup Keys del servidor a ejecutivos y no se comparten códigos por WhatsApp.

## Instalación en Windows

1. Abrir [Descargar NetBird](https://netbird.io/download/).
2. Descargar e instalar **NetBird para Windows**.
3. Abrir NetBird desde el menú Inicio y pulsar **Connect**.
4. El navegador abrirá el inicio de sesión. Entrar con el mismo correo que recibió la invitación.
5. Completar el segundo factor de Google o Microsoft si lo solicita.
6. Confirmar que NetBird muestre **Connected**.
7. Abrir Chrome y entrar a la URL privada entregada por Oscar.
8. Iniciar sesión con las credenciales personales del CRM.

## Instalación en macOS

1. Instalar desde [NetBird para macOS](https://netbird.io/download/).
2. Abrir NetBird y aceptar el permiso para agregar una configuración VPN.
3. Pulsar **Connect** e iniciar sesión con el correo invitado.
4. Confirmar **Connected** y abrir la URL privada del CRM.

## Instalación en teléfono

1. Instalar **NetBird** desde App Store o Google Play usando los enlaces de [descarga oficial](https://netbird.io/download/).
2. Abrir la aplicación, pulsar **Connect** y permitir la configuración VPN.
3. Iniciar sesión con el correo invitado.
4. Confirmar **Connected** y abrir la URL privada en el navegador del teléfono.

## Prueba obligatoria de cada persona

1. Desconectarse del Wi-Fi de la oficina y usar datos móviles o una red externa.
2. Confirmar que NetBird diga **Connected**.
3. Abrir la URL privada y entrar al CRM.
4. Crear un lead de prueba claramente marcado.
5. Cambiar su estado, crear una cotización, previsualizarla y descargarla.
6. Cerrar sesión y avisar a Oscar el resultado. Oscar elimina los datos de prueba.

## Si no conecta

1. Revisar que exista Internet normal.
2. Desconectar y volver a conectar NetBird.
3. Verificar que el correo sea exactamente el invitado.
4. Reiniciar la aplicación; no reinstalar ni crear otra cuenta sin avisar.
5. Enviar a Oscar una captura de **NetBird > Status**, sin mostrar contraseñas.

## Baja o pérdida de equipo

Oscar debe hacer las tres acciones siguientes:

1. Eliminar el equipo en **NetBird > Peers**.
2. Retirar a la persona en **NetBird > Team** si deja la empresa.
3. Desactivar su usuario dentro del CRM.

Las invitaciones y los equipos no se renuevan mensualmente. El administrador puede revocar el acceso inmediatamente. Cada nuevo teléfono o notebook aparece como un equipo separado.

## Procedimiento del administrador — lunes

1. Entrar a [NetBird Dashboard](https://app.netbird.io/) con `oscar@greendiamond.cl`.
2. Ir a **Peers > Servers > Add Peer** y generar una **one-off Setup Key** para `CRM-SERVIDOR`.
3. En Ubuntu ejecutar:

   ```bash
   cd /opt/greendiamond/crm
   sudo bash deploy/ubuntu/setup-netbird-crm.sh
   ```

4. Pegar la Setup Key cuando la solicite. No guardarla en un archivo ni en el historial.
5. Anotar la `URL_CRM` que devuelve el instalador.
6. En **Access Control**, crear un grupo `CRM-USUARIOS`; incorporar el servidor y los usuarios autorizados.
7. Permitir al grupo únicamente acceso TCP a puertos 80 y 443 del servidor. SSH (22) queda reservado para Oscar.
8. En **Team**, invitar los tres correos individuales.
9. Realizar primero la prueba con Oscar desde datos móviles y luego con una ejecutiva.

## Límite del plan

El plan Free admite cinco usuarios. Oscar + tres ejecutivos ocupan cuatro. Antes de agregar una sexta identidad se debe reevaluar el plan o migrar el control de NetBird a una instalación propia. Los equipos de un mismo usuario no crean usuarios adicionales, pero sí deben quedar registrados y reconocibles.
