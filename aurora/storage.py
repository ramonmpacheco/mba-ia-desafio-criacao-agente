"""Regras transacionais; nenhum acesso ao modelo é necessário aqui."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from uuid import uuid4


class Store:
    def __init__(self, settings):
        self.path = settings.state_dir / "condominio.db"
        self.data_dir = settings.data_dir
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS apartamentos(numero TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS areas(id TEXT PRIMARY KEY, nome TEXT, taxa REAL);
                CREATE TABLE IF NOT EXISTS vinculos(session_id TEXT PRIMARY KEY, apartamento TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reservas(
                    codigo TEXT PRIMARY KEY, apartamento TEXT NOT NULL,
                    area TEXT NOT NULL, data TEXT NOT NULL, ativa INTEGER NOT NULL DEFAULT 1);
                CREATE UNIQUE INDEX IF NOT EXISTS reserva_exclusiva ON reservas(area,data) WHERE ativa=1;
                CREATE TABLE IF NOT EXISTS visitantes(
                    id TEXT PRIMARY KEY, apartamento TEXT NOT NULL, nome TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS confirmacoes(
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, invocation_id TEXT NOT NULL,
                    tool_id TEXT NOT NULL, acao TEXT NOT NULL, detalhes TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pendente');
                CREATE TABLE IF NOT EXISTS efeitos(chave TEXT PRIMARY KEY, resultado TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta(chave TEXT PRIMARY KEY);
            """)
            if not db.execute("SELECT 1 FROM meta WHERE chave='inicializado'").fetchone():
                self.seed(db)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def seed(self, db):
        def read(name):
            return json.loads((self.data_dir / f"{name}.json").read_text())

        db.executemany(
            "INSERT OR IGNORE INTO apartamentos VALUES (?)",
            [(a["numero"],) for a in read("apartamentos")],
        )
        db.executemany("INSERT OR REPLACE INTO areas VALUES (:id,:nome,:taxa)", read("areas"))
        db.executemany(
            "INSERT INTO reservas(codigo,apartamento,area,data) VALUES (:codigo,:apartamento,:area,:data)",
            read("reservas"),
        )
        for v in read("visitantes"):
            db.execute(
                "INSERT INTO visitantes VALUES (?,?,?,?)",
                (uuid4().hex, v["apartamento"], v["nome"], v["data"]),
            )
        db.execute("INSERT OR IGNORE INTO meta VALUES ('inicializado')")

    def apartment_exists(self, apartamento):
        with self.connect() as db:
            return bool(
                db.execute("SELECT 1 FROM apartamentos WHERE numero=?", (apartamento,)).fetchone()
            )

    def bind(self, session_id, apartamento):
        with self.connect() as db:
            db.execute("INSERT INTO vinculos VALUES (?,?)", (session_id, apartamento))

    def apartment(self, session_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT apartamento FROM vinculos WHERE session_id=?", (session_id,)
            ).fetchone()
            return row["apartamento"] if row else None

    def areas(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM areas ORDER BY id")]

    def reservations(self, apartamento):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT codigo,area,data FROM reservas WHERE apartamento=? AND ativa=1 ORDER BY data,codigo",
                    (apartamento,),
                )
            ]

    def visitors(self, apartamento):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT nome,data FROM visitantes WHERE apartamento=? ORDER BY data,nome",
                    (apartamento,),
                )
            ]

    def validate_area_date(self, area, day):
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except ValueError:
            return {"erro": "Data inválida. Use AAAA-MM-DD."}
        with self.connect() as db:
            row = db.execute("SELECT * FROM areas WHERE id=?", (area,)).fetchone()
        return dict(row) if row else {"erro": "Área inexistente."}

    def availability(self, area, day):
        with self.connect() as db:
            return not bool(
                db.execute(
                    "SELECT 1 FROM reservas WHERE area=? AND data=? AND ativa=1", (area, day)
                ).fetchone()
            )

    def reserve(self, apartamento, area, day, operation):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT resultado FROM efeitos WHERE chave=?", (operation,)).fetchone()
            if old:
                return json.loads(old["resultado"])
            code = "RSV-" + uuid4().hex
            try:
                db.execute(
                    "INSERT INTO reservas(codigo,apartamento,area,data) VALUES (?,?,?,?)",
                    (code, apartamento, area, day),
                )
                result = {"status": "reservada", "codigo": code, "area": area, "data": day}
            except sqlite3.IntegrityError:
                result = {
                    "status": "indisponivel",
                    "mensagem": "Esta área já está ocupada nessa data.",
                }
            db.execute("INSERT INTO efeitos VALUES (?,?)", (operation, json.dumps(result)))
            return result

    def cancel(self, apartamento, area, day):
        with self.connect() as db:
            n = db.execute(
                "UPDATE reservas SET ativa=0 WHERE apartamento=? AND area=? AND data=? AND ativa=1",
                (apartamento, area, day),
            ).rowcount
            return {"status": "cancelada" if n else "nao_encontrada"}

    def authorize(self, apartamento, name, day, operation):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT resultado FROM efeitos WHERE chave=?", (operation,)).fetchone()
            if old:
                return json.loads(old["resultado"])
            db.execute(
                "INSERT INTO visitantes VALUES (?,?,?,?)", (uuid4().hex, apartamento, name, day)
            )
            result = {"status": "autorizado", "nome": name, "data": day}
            db.execute("INSERT INTO efeitos VALUES (?,?)", (operation, json.dumps(result)))
            return result

    def add_confirmation(self, *, id, session_id, invocation_id, tool_id, acao, detalhes):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO confirmacoes(id,session_id,invocation_id,tool_id,acao,detalhes) VALUES (?,?,?,?,?,?)",
                (id, session_id, invocation_id, tool_id, acao, json.dumps(detalhes)),
            )

    def pending(self, session_id):
        with self.connect() as db:
            return [
                {"id": r["id"], "acao": r["acao"], "detalhes": json.loads(r["detalhes"])}
                for r in db.execute(
                    "SELECT * FROM confirmacoes WHERE session_id=? AND status='pendente' ORDER BY rowid",
                    (session_id,),
                )
            ]

    def claim_confirmation(self, session_id, id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM confirmacoes WHERE id=? AND session_id=? AND status='pendente'",
                (id, session_id),
            ).fetchone()
            if not row:
                return None
            db.execute("UPDATE confirmacoes SET status='respondendo' WHERE id=?", (id,))
            result = dict(row)
            result["detalhes"] = json.loads(result["detalhes"])
            return result

    def finish_confirmation(self, id):
        with self.connect() as db:
            db.execute("UPDATE confirmacoes SET status='respondida' WHERE id=?", (id,))
