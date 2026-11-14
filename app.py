import sqlite3
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import sqlite3
from flask import request, jsonify, render_template_string
from pey import crear_cobro

from flask import Flask, abort, flash, g, redirect, render_template, request, url_for

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "cuenta.db"

app = Flask(__name__)
# Solo sirve para los mensajes cortos (flash) entre páginas. No es una contraseña.
app.config["SECRET_KEY"] = "demo-local-hackathon"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    tip_percent INTEGER NOT NULL DEFAULT 0,
    paid INTEGER NOT NULL DEFAULT 0,
    paid_amount_cents INTEGER
);

CREATE TABLE IF NOT EXISTS selections (
    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    PRIMARY KEY (participant_id, product_id)
);
"""


def init_db():
    """Crea el archivo cuenta.db y las tablas si todavía no existen."""
    connection = sqlite3.connect(DATABASE)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()


def get_db():
    """Abre una conexión para este pedido y la reutiliza hasta que termina."""
    database = g.get("_db")
    if database is None:
        database = sqlite3.connect(DATABASE, timeout=5)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys = ON")
        g._db = database
    return database


@app.teardown_appcontext
def close_db(_exception):
    database = g.pop("_db", None)
    if database is not None:
        database.close()


def format_money(cents):
    cents = int(cents or 0)
    sign = "-" if cents < 0 else ""
    pesos, dec = divmod(abs(cents), 100)
    return f"{sign}${pesos:,}.{dec:02d}"


@app.template_filter("money")
def money_filter(cents):
    return format_money(cents)


def clean_name(raw):
    name = " ".join((raw or "").split())
    if not name:
        raise ValueError("El nombre no puede ir vacío.")
    if len(name) > 80:
        raise ValueError("El nombre es demasiado largo (máximo 80 caracteres).")
    return name


def parse_pesos(raw):
    text = (raw or "").strip().replace("$", "").replace(" ", "")
    if not text:
        raise ValueError("Escribe un precio.")
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise ValueError("Ese precio no se entiende. Usa algo como 85 o 85.50.") from error
    if amount < 0:
        raise ValueError("El precio no puede ser negativo.")
    cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents > 100_000_000:
        raise ValueError("Ese precio es demasiado alto para esta demo.")
    return cents


def parse_tip(raw):
    text = (raw or "0").strip()
    if text == "":
        return 0
    if not text.isdigit():
        raise ValueError("La propina tiene que ser un número entero entre 0 y 100.")
    tip = int(text)
    if tip > 100:
        raise ValueError("La propina tiene que ser un número entero entre 0 y 100.")
    return tip


def split_price(price_cents, participant_ids):
    """Reparte un precio en centavos. Si sobran centavos, van de uno en uno."""
    ordered = sorted(participant_ids)
    count = len(ordered)
    if count == 0:
        return {}
    base, remainder = divmod(int(price_cents), count)
    return {
        participant_id: base + (1 if index < remainder else 0)
        for index, participant_id in enumerate(ordered)
    }


def tip_cents(subtotal_cents, tip_percent):
    return (int(subtotal_cents) * int(tip_percent or 0) + 50) // 100


def load_account_view(account_id):
    database = get_db()
    account = database.execute(
        "SELECT * FROM accounts WHERE id = ?",
        (account_id,),
    ).fetchone()
    if account is None:
        abort(404)

    products = [
        dict(row)
        for row in database.execute(
            "SELECT * FROM products WHERE account_id = ? ORDER BY id",
            (account_id,),
        )
    ]
    participants = [
        dict(row)
        for row in database.execute(
            "SELECT * FROM participants WHERE account_id = ? ORDER BY name COLLATE NOCASE, id",
            (account_id,),
        )
    ]
    selection_rows = database.execute(
        """
        SELECT s.participant_id, s.product_id
        FROM selections AS s
        JOIN participants AS p ON p.id = s.participant_id
        WHERE p.account_id = ?
        """,
        (account_id,),
    ).fetchall()

    selected = defaultdict(set)
    consumers = defaultdict(set)
    for row in selection_rows:
        selected[row["participant_id"]].add(row["product_id"])
        consumers[row["product_id"]].add(row["participant_id"])

    subtotals = {person["id"]: 0 for person in participants}
    for product in products:
        product_consumers = consumers[product["id"]]
        product["consumer_count"] = len(product_consumers)
        for participant_id, cents in split_price(product["price_cents"], product_consumers).items():
            subtotals[participant_id] += cents

    people = []
    for person in participants:
        subtotal = subtotals[person["id"]]
        tip = tip_cents(subtotal, person["tip_percent"])
        live = subtotal + tip
        person["subtotal_cents"] = subtotal
        person["tip_cents"] = tip
        person["live_cents"] = live
        if person["paid"] and person["paid_amount_cents"] is not None:
            person["display_cents"] = person["paid_amount_cents"]
        else:
            person["display_cents"] = live
        person["product_names"] = [
            product["name"] for product in products if product["id"] in selected[person["id"]]
        ]
        people.append(person)

    paid_people = [person for person in people if person["paid"]]
    pending_people = [person for person in people if not person["paid"]]
    return {
        "account": dict(account),
        "products": products,
        "people": people,
        "paid_people": paid_people,
        "pending_people": pending_people,
        "products_total": sum(product["price_cents"] for product in products),
        "unassigned": sum(
            product["price_cents"] for product in products if product["consumer_count"] == 0
        ),
        "paid_total": sum(person["display_cents"] for person in paid_people),
        "due_total": sum(person["live_cents"] for person in pending_people),
        "selected": selected,
        "consumers": consumers,
    }


def product_choices(view, participant_id):
    choices = []
    selected_ids = view["selected"][participant_id]
    for product in view["products"]:
        consumers = set(view["consumers"][product["id"]])
        checked = product["id"] in selected_ids
        group = consumers if checked else consumers | {participant_id}
        share = split_price(product["price_cents"], group).get(participant_id, 0)
        others = len(consumers) - (1 if checked else 0)
        choices.append(
            {
                **product,
                "checked": checked,
                "share_cents": share,
                "others": others,
            }
        )
    return choices


def find_person(view, participant_id):
    for person in view["people"]:
        if person["id"] == participant_id:
            return person
    abort(404)


@app.route("/")
def inicio():
    accounts = get_db().execute(
        """
        SELECT
            a.*,
            (SELECT COUNT(*) FROM products p WHERE p.account_id = a.id) AS product_count,
            (SELECT COUNT(*) FROM participants pa WHERE pa.account_id = a.id) AS participant_count
        FROM accounts AS a
        ORDER BY a.id DESC
        """
    ).fetchall()
    return render_template("index.html", accounts=accounts)


@app.route("/cuentas", methods=["POST"])
def crear_cuenta():
    try:
        name = clean_name(request.form.get("name"))
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("inicio"))

    database = get_db()
    cursor = database.execute(
        "INSERT INTO accounts (name, created_at) VALUES (?, ?)",
        (name, datetime.now().strftime("%Y-%m-%d %H:%M")),
    )
    database.commit()
    return redirect(url_for("ver_cuenta", account_id=cursor.lastrowid))


@app.route("/cuentas/<int:account_id>")
def ver_cuenta(account_id):
    return render_template("cuenta.html", **load_account_view(account_id))


@app.route("/cuentas/<int:account_id>/productos", methods=["POST"])
def agregar_producto(account_id):
    load_account_view(account_id)
    try:
        name = clean_name(request.form.get("name"))
        price_cents = parse_pesos(request.form.get("price"))
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("ver_cuenta", account_id=account_id))

    database = get_db()
    database.execute(
        "INSERT INTO products (account_id, name, price_cents) VALUES (?, ?, ?)",
        (account_id, name, price_cents),
    )
    database.commit()
    flash(f"Agregaste {name}.")
    return redirect(url_for("ver_cuenta", account_id=account_id))


@app.route("/cuentas/<int:account_id>/productos/<int:product_id>/quitar", methods=["POST"])
def quitar_producto(account_id, product_id):
    database = get_db()
    product = database.execute(
        "SELECT name FROM products WHERE id = ? AND account_id = ?",
        (product_id, account_id),
    ).fetchone()
    if product is None:
        abort(404)
    database.execute("DELETE FROM products WHERE id = ?", (product_id,))
    database.commit()
    flash(f"Quitaste {product['name']}. Quien ya pagó conserva el monto que tenía.")
    return redirect(url_for("ver_cuenta", account_id=account_id))


@app.route("/cuentas/<int:account_id>/entrar", methods=["POST"])
def entrar(account_id):
    load_account_view(account_id)
    try:
        name = clean_name(request.form.get("name"))
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("ver_cuenta", account_id=account_id))

    database = get_db()
    existing = database.execute(
        "SELECT id, name FROM participants WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    for person in existing:
        if person["name"].casefold() == name.casefold():
            return redirect(
                url_for("ver_participante", account_id=account_id, participant_id=person["id"])
            )

    cursor = database.execute(
        "INSERT INTO participants (account_id, name) VALUES (?, ?)",
        (account_id, name),
    )
    database.commit()
    return redirect(
        url_for("ver_participante", account_id=account_id, participant_id=cursor.lastrowid)
    )


@app.route("/cuentas/<int:account_id>/p/<int:participant_id>")
def ver_participante(account_id, participant_id):
    view = load_account_view(account_id)
    person = find_person(view, participant_id)
    return render_template(
        "participante.html",
        account=view["account"],
        person=person,
        choices=product_choices(view, participant_id),
    )


@app.route("/cuentas/<int:account_id>/p/<int:participant_id>/guardar", methods=["POST"])
def guardar_consumo(account_id, participant_id):
    view = load_account_view(account_id)
    person = find_person(view, participant_id)
    if person["paid"]:
        flash("Este pago ya está marcado. Deshazlo en el tablero si quieres cambiar algo.")
        return redirect(
            url_for("ver_participante", account_id=account_id, participant_id=participant_id)
        )

    try:
        tip = parse_tip(request.form.get("tip_percent"))
    except ValueError as error:
        flash(str(error))
        return redirect(
            url_for("ver_participante", account_id=account_id, participant_id=participant_id)
        )

    valid_ids = {product["id"] for product in view["products"]}
    chosen = []
    for raw in request.form.getlist("product_id"):
        if raw.isdigit() and int(raw) in valid_ids:
            chosen.append(int(raw))

    database = get_db()
    database.execute("DELETE FROM selections WHERE participant_id = ?", (participant_id,))
    database.executemany(
        "INSERT INTO selections (participant_id, product_id) VALUES (?, ?)",
        [(participant_id, product_id) for product_id in chosen],
    )
    database.execute(
        "UPDATE participants SET tip_percent = ? WHERE id = ?",
        (tip, participant_id),
    )
    database.commit()

    updated = load_account_view(account_id)
    amount = find_person(updated, participant_id)["live_cents"]
    flash(f"Guardado. Te toca {format_money(amount)}.")
    return redirect(
        url_for("ver_participante", account_id=account_id, participant_id=participant_id)
    )

QR_PAGE = """
<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<body style="font-family:sans-serif;text-align:center;padding:24px">
<h2>{{ person['name'] }}, escanea para pagar</h2>
<img src="{{ qr }}" width="260" alt="QR de pago">
<p>Le toca: {{ monto }}</p>
<p id="estado">Esperando el pago…</p>
<a href="{{ url_for('ver_cuenta', account_id=account_id) }}">Volver a la cuenta</a>
<script>
setInterval(async () => {
  const r = await fetch("{{ url_for('estado_pago', participant_id=participant_id) }}");
  const d = await r.json();
  if (d.paid) {
    document.getElementById("estado").textContent = "✔ Pago recibido";
    setTimeout(() => location.href = "{{ url_for('ver_cuenta', account_id=account_id) }}", 1200);
  }
}, 3000);
</script>
"""

def asegurar_tablas_pey(db):
    db.execute("CREATE TABLE IF NOT EXISTS pey_cobros (participant_id INTEGER PRIMARY KEY, cents INTEGER, charge_id TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS pey_pagos (transaction_id TEXT PRIMARY KEY, participant_id INTEGER)")

@app.route("/cuentas/<int:account_id>/p/<int:participant_id>/pagar", methods=["POST"])
def pagar(account_id, participant_id):
    view = load_account_view(account_id)
    person = find_person(view, participant_id)
    if person["paid"]:
        flash(f"{person['name']} ya estaba como pagado.")
        return redirect(url_for("ver_cuenta", account_id=account_id))
    if not person["product_names"]:
        flash("Marca al menos un producto antes de pagar.")
        return redirect(
            url_for("ver_participante", account_id=account_id, participant_id=participant_id)
        )

    try:
        charge_id, qr = crear_cobro(
            participant_id,
            person["live_cents"] / 100,
            f"Cuenta compartida - {person['name']}",
        )
    except Exception:
        flash("No se pudo crear el cobro con pey. Intenta de nuevo.")
        return redirect(url_for("ver_cuenta", account_id=account_id))

    db = get_db()
    asegurar_tablas_pey(db)
    db.execute(
        "INSERT OR REPLACE INTO pey_cobros (participant_id, cents, charge_id) VALUES (?, ?, ?)",
        (participant_id, person["live_cents"], charge_id),
    )
    db.commit()
    return render_template_string(
        QR_PAGE, person=person, qr=qr, account_id=account_id,
        participant_id=participant_id, monto=format_money(person["live_cents"]),
    )

@app.route("/participantes/<int:participant_id>/estado")
def estado_pago(participant_id):
    row = get_db().execute("SELECT paid FROM participants WHERE id = ?", (participant_id,)).fetchone()
    return jsonify(paid=bool(row and row[0]))

@app.route("/webhooks", methods=["POST"])
def webhooks():
    data = request.get_json(silent=True) or {}
    transaction_id = data.get("transactionId")
    num_ref = str(data.get("numRef", ""))
    if not transaction_id or not num_ref.isdigit():
        return "", 400
    participant_id = int(num_ref)

    db = get_db()
    asegurar_tablas_pey(db)
    cobro = db.execute(
        "SELECT cents FROM pey_cobros WHERE participant_id = ?", (participant_id,)
    ).fetchone()
    if cobro is None:
        return "", 404
    try:
        db.execute("INSERT INTO pey_pagos VALUES (?, ?)", (transaction_id, participant_id))
    except sqlite3.IntegrityError:
        return "", 200  # este pago ya se procesó, no lo repetimos
    db.execute(
        "UPDATE participants SET paid = 1, paid_amount_cents = ? WHERE id = ?",
        (cobro[0], participant_id),
    )
    db.commit()
    return "", 200

@app.route("/cuentas/<int:account_id>/p/<int:participant_id>/deshacer", methods=["POST"])
def deshacer_pago(account_id, participant_id):
    view = load_account_view(account_id)
    person = find_person(view, participant_id)
    database = get_db()
    database.execute(
        "UPDATE participants SET paid = 0, paid_amount_cents = NULL WHERE id = ?",
        (participant_id,),
    )
    database.commit()
    flash(f"{person['name']} vuelve a estar pendiente.")
    return redirect(url_for("ver_cuenta", account_id=account_id))


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.errorhandler(404)
def not_found(_error):
    return render_template("404.html"), 404


init_db()

if __name__ == "__main__":
    print("\nAbre esta dirección en el navegador: http://127.0.0.1:5000\n")
    app.run(debug=True, host="127.0.0.1", port=5000)