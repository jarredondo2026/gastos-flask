import csv
import io
import os
import sqlite3
from datetime import date, datetime
from functools import wraps
from flask import Flask, Response, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "gastos.db")
USUARIO_INICIAL = "admin"
CLAVE_INICIAL = "123"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "clave-secreta-control-de-gastos")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS usuario (
            id_usuario INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gasto (
            id_gasto INTEGER PRIMARY KEY AUTOINCREMENT,
            id_usuario INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            monto REAL NOT NULL CHECK (monto > 0),
            FOREIGN KEY (id_usuario) REFERENCES usuario (id_usuario)
        );
        """
    )
    if con.execute("SELECT 1 FROM usuario WHERE usuario = ?", (USUARIO_INICIAL,)).fetchone() is None:
        con.execute(
            "INSERT INTO usuario (usuario, password_hash) VALUES (?, ?)",
            (USUARIO_INICIAL, generate_password_hash(CLAVE_INICIAL)),
        )
    con.commit()
    con.close()


def login_required(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if "id_usuario" not in session:
            flash("Debe iniciar sesión para continuar.", "error")
            return redirect(url_for("login"))
        return vista(*args, **kwargs)
    return envoltura


def parse_fecha(valor):
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def parse_monto(valor):
    try:
        monto = float(str(valor).strip().replace(",", "."))
    except ValueError:
        return None
    if monto != monto or monto <= 0 or monto > 1000000000:
        return None
    return round(monto, 2)


def leer_filtros():
    desde = request.args.get("desde", "").strip()
    hasta = request.args.get("hasta", "").strip()
    if desde and not parse_fecha(desde):
        flash("La fecha 'desde' no es válida.", "error")
        desde = ""
    if hasta and not parse_fecha(hasta):
        flash("La fecha 'hasta' no es válida.", "error")
        hasta = ""
    if desde and hasta and desde > hasta:
        flash("La fecha 'desde' no puede ser mayor que la fecha 'hasta'.", "error")
        desde = ""
        hasta = ""
    return desde, hasta


def consultar(db, id_usuario, desde, hasta):
    sql = "SELECT id_gasto, fecha, descripcion, monto FROM gasto WHERE id_usuario = ?"
    params = [id_usuario]
    if desde:
        sql += " AND fecha >= ?"
        params.append(desde)
    if hasta:
        sql += " AND fecha <= ?"
        params.append(hasta)
    sql += " ORDER BY fecha DESC, id_gasto DESC"
    return db.execute(sql, params).fetchall()


def resumen(filas):
    if not filas:
        return {"total": 0, "cantidad": 0, "promedio": 0, "mayor": None, "meses": []}
    total = round(sum(f["monto"] for f in filas), 2)
    meses = {}
    for f in filas:
        clave = f["fecha"][:7]
        acumulado = meses.setdefault(clave, {"mes": clave, "cantidad": 0, "total": 0})
        acumulado["cantidad"] += 1
        acumulado["total"] += f["monto"]
    lista = sorted(meses.values(), key=lambda m: m["mes"], reverse=True)
    for m in lista:
        m["total"] = round(m["total"], 2)
    return {
        "total": total,
        "cantidad": len(filas),
        "promedio": round(total / len(filas), 2),
        "mayor": max(filas, key=lambda f: f["monto"]),
        "meses": lista,
    }


def neutralizar_celda(texto):
    if texto and texto[0] in "=+-@":
        return "'" + texto
    return texto


@app.route("/")
def index():
    if "id_usuario" in session:
        return redirect(url_for("gastos"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        clave = request.form.get("clave", "")
        fila = get_db().execute("SELECT * FROM usuario WHERE usuario = ?", (usuario,)).fetchone()
        if fila and check_password_hash(fila["password_hash"], clave):
            session.clear()
            session["id_usuario"] = fila["id_usuario"]
            session["usuario"] = fila["usuario"]
            return redirect(url_for("gastos"))
        flash("Usuario o clave incorrectos.", "error")
        return render_template("login.html"), 401
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada correctamente.", "ok")
    return redirect(url_for("login"))


@app.route("/gastos", methods=["GET", "POST"])
@login_required
def gastos():
    db = get_db()
    if request.method == "POST":
        fecha_texto = request.form.get("fecha", "").strip()
        descripcion = " ".join(request.form.get("descripcion", "").split())
        monto = parse_monto(request.form.get("monto", ""))
        errores = []
        fecha = parse_fecha(fecha_texto)
        if fecha is None:
            errores.append("La fecha no es válida.")
        elif fecha > date.today():
            errores.append("La fecha no puede ser futura.")
        if not (3 <= len(descripcion) <= 120):
            errores.append("La descripción debe tener entre 3 y 120 caracteres.")
        if monto is None:
            errores.append("El monto debe ser un número mayor que 0.")
        if errores:
            for error in errores:
                flash(error, "error")
        else:
            db.execute(
                "INSERT INTO gasto (id_usuario, fecha, descripcion, monto) VALUES (?, ?, ?, ?)",
                (session["id_usuario"], fecha.isoformat(), descripcion, monto),
            )
            db.commit()
            flash("Gasto registrado correctamente.", "ok")
            return redirect(url_for("gastos"))
    filas = consultar(db, session["id_usuario"], "", "")
    total = round(sum(f["monto"] for f in filas), 2)
    return render_template("gastos.html", filas=filas, total=total, hoy=date.today().isoformat())


@app.route("/gastos/<int:id_gasto>/eliminar", methods=["POST"])
@login_required
def eliminar(id_gasto):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM gasto WHERE id_gasto = ? AND id_usuario = ?",
        (id_gasto, session["id_usuario"]),
    )
    db.commit()
    if cursor.rowcount:
        flash("Gasto eliminado.", "ok")
    else:
        flash("El gasto indicado no existe.", "error")
    return redirect(url_for("gastos"))


@app.route("/reporte")
@login_required
def reporte():
    desde, hasta = leer_filtros()
    filas = consultar(get_db(), session["id_usuario"], desde, hasta)
    return render_template(
        "reporte.html",
        filas=filas,
        datos=resumen(filas),
        desde=desde,
        hasta=hasta,
        generado=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )


@app.route("/reporte/csv")
@login_required
def reporte_csv():
    desde, hasta = leer_filtros()
    filas = consultar(get_db(), session["id_usuario"], desde, hasta)
    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow(["Fecha", "Descripción", "Monto (S/)"])
    for f in filas:
        escritor.writerow([f["fecha"], neutralizar_celda(f["descripcion"]), "%.2f" % f["monto"]])
    escritor.writerow(["", "TOTAL", "%.2f" % round(sum(f["monto"] for f in filas), 2)])
    return Response(
        "\ufeff" + salida.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=reporte_gastos.csv"},
    )


@app.errorhandler(404)
def error_404(error):
    return render_template(
        "error.html",
        codigo=404,
        titulo="Página no encontrada",
        mensaje="La ruta solicitada no existe.",
    ), 404


init_db()

if __name__ == "__main__":
    app.run(debug=True)
