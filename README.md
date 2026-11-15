Cuenta compartida

Hice una app para dividir la cuenta de un evento. Cada persona marca lo que consumió, ve cuánto le toca (con 10% de propina) y paga con un QR de CoDi. Cuando pey avisa que el pago llegó, la página se actualiza sola.

Es mi proyecto para el Hackatón México 2026 (pagos con CoDi / pey).

App en vivo: https://cuenta-compartida-nf3b.onrender.com
Código: https://github.com/garciajaimesluisfernando-afk/cuenta_compartida

La app está en el plan gratis de Render y se duerme cuando nadie la usa. La primera vez puede tardar cerca de un minuto en abrir.

El problema

En una cena o un evento en grupo, dividir la cuenta es un problema. Alguien paga todo y luego tiene que cobrarle a los demás, o se divide en partes iguales aunque cada quien consumió cosas distintas.

Cómo lo resuelvo
Se crea una cuenta con los productos del evento.
Cada persona marca lo que consumió.
La app calcula cuánto le toca, con 10% de propina.
Cada persona paga con un QR de CoDi.
Cuando pey avisa del pago, la página de esa persona se actualiza sola y el tablero muestra quién ya pagó.
Cómo funciona el pago
pey.py crea el cobro con POST https://api.pey.mx/codi/qr y dibuja el QR con la librería qrcode. Uso el id de cada persona como numRef.
La ruta /webhooks recibe el aviso de pey y marca a esa persona como pagada.
Guardo el transactionId para no procesar dos veces el mismo pago.
La página de pago revisa /participantes/<id>/estado cada pocos segundos y, cuando ya pagó, me manda al tablero.
Tecnologías

Python 3.12, Flask, SQLite, la librería qrcode, la API de pey y Render.

Archivos
app.py            rutas y lógica
pey.py            conexión con pey y QR
templates/        páginas HTML
static/           estilos
requirements.txt  dependencias
Variables de entorno
Variable	Para qué sirve
PEY_API_KEY	Mi API key de pey (no está en el repo)
MONTO_PRUEBA	Si la pongo en 0.01, todos los cobros salen de $0.01. La uso para la demo.
Correrlo en mi computadora
pip install -r requirements.txt
python app.py

Primero hay que definir PEY_API_KEY. Para recibir avisos de pey se necesita una URL pública, por eso lo tengo en Render.

Lo que probé

Hice un pago real de $0.01 por CoDi. Llegó POST /webhooks, la app respondió 200 y la página se actualizó sola.

Siguientes pasos
Revisar la firma webhook-signature con el secreto de pey en /webhooks, para confirmar que cada aviso de pago de verdad viene de pey.
