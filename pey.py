import os, io, base64, requests, qrcode
from dotenv import load_dotenv

load_dotenv()
API = "https://api.pey.mx/codi/qr"

def crear_cobro(num_ref, monto, concepto):
    monto = float(os.getenv("MONTO_PRUEBA", monto))
    r = requests.post(
        API,
        headers={"Authorization": f"Bearer {os.getenv('PEY_API_KEY')}"},
        json={"amount": monto, "concept": concepto,
              "expiration": 600, "numRef": str(num_ref)},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    buf = io.BytesIO()
    qrcode.make(data["qrString"]).save(buf, format="PNG")
    png = base64.b64encode(buf.getvalue()).decode()
    return data["id"], "data:image/png;base64," + png