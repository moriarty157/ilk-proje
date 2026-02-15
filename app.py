from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from flask import Flask, Response, g, redirect, render_template, request, url_for

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "accounting.db"

app = Flask(__name__)


PAYMENT_TYPES = ["Nakit", "Kredi Kartı", "Banka Havale", "EFT", "Çek", "Senet"]
INVOICE_TYPES = ["Girdi (Alış)", "Çıktı (Satış)"]
DOCUMENT_TYPES = ["Çek", "Senet"]


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_: Any) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            tax_number TEXT,
            phone TEXT,
            email TEXT,
            balance REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            invoice_type TEXT NOT NULL,
            invoice_date TEXT NOT NULL,
            due_date TEXT,
            notes TEXT,
            total_amount REAL NOT NULL,
            FOREIGN KEY(customer_id) REFERENCES customers(id)
        );

        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit_price REAL NOT NULL,
            vat_rate REAL NOT NULL,
            line_total REAL NOT NULL,
            FOREIGN KEY(invoice_id) REFERENCES invoices(id)
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            payment_type TEXT NOT NULL,
            payment_date TEXT NOT NULL,
            amount REAL NOT NULL,
            direction TEXT NOT NULL,
            description TEXT,
            FOREIGN KEY(customer_id) REFERENCES customers(id)
        );

        CREATE TABLE IF NOT EXISTS instruments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            document_type TEXT NOT NULL,
            document_no TEXT NOT NULL,
            issue_date TEXT NOT NULL,
            due_date TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY(customer_id) REFERENCES customers(id)
        );
        """
    )
    db.commit()
    db.close()


def update_customer_balance(customer_id: int) -> None:
    db = get_db()
    sales_total = db.execute(
        "SELECT COALESCE(SUM(total_amount), 0) FROM invoices WHERE customer_id = ? AND invoice_type = ?",
        (customer_id, "Çıktı (Satış)"),
    ).fetchone()[0]
    purchase_total = db.execute(
        "SELECT COALESCE(SUM(total_amount), 0) FROM invoices WHERE customer_id = ? AND invoice_type = ?",
        (customer_id, "Girdi (Alış)"),
    ).fetchone()[0]

    incoming_payments = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE customer_id = ? AND direction = 'Tahsilat'",
        (customer_id,),
    ).fetchone()[0]

    outgoing_payments = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE customer_id = ? AND direction = 'Ödeme'",
        (customer_id,),
    ).fetchone()[0]

    balance = sales_total - purchase_total - incoming_payments + outgoing_payments
    db.execute("UPDATE customers SET balance = ? WHERE id = ?", (balance, customer_id))
    db.commit()


@app.route("/")
def dashboard() -> str:
    db = get_db()
    totals = {
        "customers": db.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "invoices": db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0],
        "payments": db.execute("SELECT COALESCE(SUM(amount), 0) FROM payments").fetchone()[0],
        "instruments": db.execute("SELECT COUNT(*) FROM instruments").fetchone()[0],
    }
    customers = db.execute("SELECT * FROM customers ORDER BY created_at DESC").fetchall()
    recent_invoices = db.execute(
        """
        SELECT invoices.*, customers.name AS customer_name
        FROM invoices
        JOIN customers ON customers.id = invoices.customer_id
        ORDER BY invoice_date DESC, invoices.id DESC
        LIMIT 10
        """
    ).fetchall()
    return render_template(
        "dashboard.html",
        totals=totals,
        customers=customers,
        recent_invoices=recent_invoices,
        payment_types=PAYMENT_TYPES,
        invoice_types=INVOICE_TYPES,
        document_types=DOCUMENT_TYPES,
    )


@app.post("/customers")
def create_customer() -> Response:
    db = get_db()
    db.execute(
        "INSERT INTO customers(name, tax_number, phone, email, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            request.form["name"],
            request.form.get("tax_number"),
            request.form.get("phone"),
            request.form.get("email"),
            date.today().isoformat(),
        ),
    )
    db.commit()
    return redirect(url_for("dashboard"))


@app.post("/invoices")
def create_invoice() -> Response:
    db = get_db()
    customer_id = int(request.form["customer_id"])
    qty = float(request.form["quantity"])
    unit_price = float(request.form["unit_price"])
    vat_rate = float(request.form["vat_rate"])
    net = qty * unit_price
    vat = net * vat_rate / 100
    total = net + vat

    cur = db.execute(
        """
        INSERT INTO invoices(customer_id, invoice_type, invoice_date, due_date, notes, total_amount)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            customer_id,
            request.form["invoice_type"],
            request.form["invoice_date"],
            request.form.get("due_date"),
            request.form.get("notes"),
            total,
        ),
    )
    invoice_id = cur.lastrowid
    db.execute(
        """
        INSERT INTO invoice_items(invoice_id, description, quantity, unit_price, vat_rate, line_total)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            invoice_id,
            request.form["description"],
            qty,
            unit_price,
            vat_rate,
            total,
        ),
    )
    db.commit()
    update_customer_balance(customer_id)
    return redirect(url_for("dashboard"))


@app.post("/payments")
def create_payment() -> Response:
    db = get_db()
    customer_id = int(request.form["customer_id"])
    db.execute(
        """
        INSERT INTO payments(customer_id, payment_type, payment_date, amount, direction, description)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            customer_id,
            request.form["payment_type"],
            request.form["payment_date"],
            float(request.form["amount"]),
            request.form["direction"],
            request.form.get("description"),
        ),
    )
    db.commit()
    update_customer_balance(customer_id)
    return redirect(url_for("dashboard"))


@app.post("/instruments")
def create_instrument() -> Response:
    db = get_db()
    db.execute(
        """
        INSERT INTO instruments(customer_id, document_type, document_no, issue_date, due_date, amount, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(request.form["customer_id"]),
            request.form["document_type"],
            request.form["document_no"],
            request.form["issue_date"],
            request.form["due_date"],
            float(request.form["amount"]),
            request.form["status"],
            request.form.get("notes"),
        ),
    )
    db.commit()
    return redirect(url_for("dashboard"))


@app.get("/reports/monthly")
def monthly_report() -> str:
    target_month = request.args.get("month", date.today().strftime("%Y-%m"))
    db = get_db()

    sales = db.execute(
        "SELECT COALESCE(SUM(total_amount), 0) FROM invoices WHERE invoice_type = 'Çıktı (Satış)' AND invoice_date LIKE ?",
        (f"{target_month}%",),
    ).fetchone()[0]
    purchases = db.execute(
        "SELECT COALESCE(SUM(total_amount), 0) FROM invoices WHERE invoice_type = 'Girdi (Alış)' AND invoice_date LIKE ?",
        (f"{target_month}%",),
    ).fetchone()[0]
    collections = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE direction = 'Tahsilat' AND payment_date LIKE ?",
        (f"{target_month}%",),
    ).fetchone()[0]
    outgoing = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE direction = 'Ödeme' AND payment_date LIKE ?",
        (f"{target_month}%",),
    ).fetchone()[0]

    open_instruments = db.execute(
        """
        SELECT instruments.*, customers.name AS customer_name
        FROM instruments
        JOIN customers ON customers.id = instruments.customer_id
        WHERE status != 'Tahsil/Ödendi'
        ORDER BY due_date ASC
        """
    ).fetchall()

    return render_template(
        "monthly_report.html",
        month=target_month,
        sales=sales,
        purchases=purchases,
        collections=collections,
        outgoing=outgoing,
        net_cash=collections - outgoing,
        gross_profit=sales - purchases,
        open_instruments=open_instruments,
    )


@app.get("/export/<string:dataset>")
def export_csv(dataset: str) -> Response:
    db = get_db()
    mapping = {
        "cariler": (
            "SELECT id, name, tax_number, phone, email, balance, created_at FROM customers ORDER BY id",
            ["id", "name", "tax_number", "phone", "email", "balance", "created_at"],
        ),
        "faturalar": (
            """
            SELECT invoices.id, customers.name AS customer, invoice_type, invoice_date, due_date, total_amount, notes
            FROM invoices JOIN customers ON customers.id = invoices.customer_id
            ORDER BY invoices.id
            """,
            ["id", "customer", "invoice_type", "invoice_date", "due_date", "total_amount", "notes"],
        ),
        "odemeler": (
            """
            SELECT payments.id, customers.name AS customer, payment_type, payment_date, amount, direction, description
            FROM payments JOIN customers ON customers.id = payments.customer_id
            ORDER BY payments.id
            """,
            ["id", "customer", "payment_type", "payment_date", "amount", "direction", "description"],
        ),
        "cek-senet": (
            """
            SELECT instruments.id, customers.name AS customer, document_type, document_no, issue_date, due_date, amount, status, notes
            FROM instruments JOIN customers ON customers.id = instruments.customer_id
            ORDER BY instruments.id
            """,
            ["id", "customer", "document_type", "document_no", "issue_date", "due_date", "amount", "status", "notes"],
        ),
    }
    if dataset not in mapping:
        return Response("Desteklenmeyen veri kümesi", status=404)

    query, headers = mapping[dataset]
    rows = db.execute(query).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row[h] for h in headers])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={dataset}.csv"},
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
