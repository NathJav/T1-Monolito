"""Persistência local da integração (SQLite).

Guarda três coisas:
  * vinculos  – correspondência entre o registro do i-Educar e o do Moodle,
                com o hash do último payload enviado (evita reenvios sem mudança);
  * execucoes – histórico das sincronizações e seus contadores;
  * erros     – falhas por registro, para diagnóstico e reprocessamento.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

ESQUEMA = """
CREATE TABLE IF NOT EXISTS vinculos (
    tipo          TEXT    NOT NULL,
    id_origem     TEXT    NOT NULL,
    id_moodle     INTEGER NOT NULL,
    hash          TEXT    NOT NULL,
    extra         TEXT,
    atualizado_em TEXT    NOT NULL,
    PRIMARY KEY (tipo, id_origem)
);
CREATE INDEX IF NOT EXISTS ix_vinculos_moodle ON vinculos (tipo, id_moodle);

CREATE TABLE IF NOT EXISTS execucoes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio    TEXT NOT NULL,
    fim       TEXT,
    status    TEXT NOT NULL,
    gatilho   TEXT NOT NULL,
    ano       INTEGER,
    resumo    TEXT
);

CREATE TABLE IF NOT EXISTS erros (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    execucao_id  INTEGER NOT NULL REFERENCES execucoes(id),
    entidade     TEXT NOT NULL,
    id_origem    TEXT,
    mensagem     TEXT NOT NULL,
    criado_em    TEXT NOT NULL
);
"""


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Vinculo:
    tipo: str
    id_origem: str
    id_moodle: int
    hash: str
    extra: dict
    atualizado_em: str


class Repositorio:
    def __init__(self, caminho: str):
        self.caminho = caminho
        if caminho != ":memory:":
            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        self._conexao = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None)
        self._conexao.row_factory = sqlite3.Row
        self._conexao.execute("PRAGMA journal_mode=WAL" if caminho != ":memory:" else "PRAGMA foreign_keys=ON")
        self._trava = threading.RLock()
        with self._cursor() as cur:
            cur.executescript(ESQUEMA)

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._trava:
            cur = self._conexao.cursor()
            try:
                yield cur
            finally:
                cur.close()

    # ---------------------------------------------------------------- vínculos
    def obter_vinculo(self, tipo: str, id_origem: int | str) -> Vinculo | None:
        with self._cursor() as cur:
            linha = cur.execute(
                "SELECT * FROM vinculos WHERE tipo = ? AND id_origem = ?", (tipo, str(id_origem))
            ).fetchone()
        return self._para_vinculo(linha) if linha else None

    def salvar_vinculo(
        self, tipo: str, id_origem: int | str, id_moodle: int, hash_: str, extra: dict | None = None
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO vinculos (tipo, id_origem, id_moodle, hash, extra, atualizado_em)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (tipo, id_origem) DO UPDATE SET
                    id_moodle = excluded.id_moodle,
                    hash = excluded.hash,
                    extra = excluded.extra,
                    atualizado_em = excluded.atualizado_em
                """,
                (tipo, str(id_origem), int(id_moodle), hash_, json.dumps(extra or {}), agora()),
            )

    def remover_vinculo(self, tipo: str, id_origem: int | str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM vinculos WHERE tipo = ? AND id_origem = ?", (tipo, str(id_origem)))

    def listar_vinculos(self, tipo: str | None = None, limite: int = 500) -> list[Vinculo]:
        sql = "SELECT * FROM vinculos"
        params: tuple = ()
        if tipo:
            sql += " WHERE tipo = ?"
            params = (tipo,)
        sql += " ORDER BY tipo, CAST(id_origem AS INTEGER) LIMIT ?"
        with self._cursor() as cur:
            linhas = cur.execute(sql, (*params, limite)).fetchall()
        return [self._para_vinculo(l) for l in linhas]

    def contar_vinculos(self) -> dict[str, int]:
        with self._cursor() as cur:
            linhas = cur.execute("SELECT tipo, COUNT(*) AS total FROM vinculos GROUP BY tipo").fetchall()
        return {l["tipo"]: l["total"] for l in linhas}

    @staticmethod
    def _para_vinculo(linha: sqlite3.Row) -> Vinculo:
        return Vinculo(
            tipo=linha["tipo"],
            id_origem=linha["id_origem"],
            id_moodle=linha["id_moodle"],
            hash=linha["hash"],
            extra=json.loads(linha["extra"] or "{}"),
            atualizado_em=linha["atualizado_em"],
        )

    # --------------------------------------------------------------- execuções
    def iniciar_execucao(self, gatilho: str, ano: int | None) -> int:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO execucoes (inicio, status, gatilho, ano) VALUES (?, 'em_andamento', ?, ?)",
                (agora(), gatilho, ano),
            )
            return int(cur.lastrowid)

    def finalizar_execucao(self, execucao_id: int, status: str, resumo: dict) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE execucoes SET fim = ?, status = ?, resumo = ? WHERE id = ?",
                (agora(), status, json.dumps(resumo, ensure_ascii=False), execucao_id),
            )

    def registrar_erro(self, execucao_id: int, entidade: str, id_origem: str | None, mensagem: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO erros (execucao_id, entidade, id_origem, mensagem, criado_em) VALUES (?, ?, ?, ?, ?)",
                (execucao_id, entidade, id_origem, mensagem[:2000], agora()),
            )

    def listar_execucoes(self, limite: int = 20) -> list[dict]:
        with self._cursor() as cur:
            linhas = cur.execute("SELECT * FROM execucoes ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [self._execucao(l) for l in linhas]

    def obter_execucao(self, execucao_id: int) -> dict | None:
        with self._cursor() as cur:
            linha = cur.execute("SELECT * FROM execucoes WHERE id = ?", (execucao_id,)).fetchone()
            if not linha:
                return None
            erros = cur.execute(
                "SELECT entidade, id_origem, mensagem, criado_em FROM erros WHERE execucao_id = ? ORDER BY id",
                (execucao_id,),
            ).fetchall()
        execucao = self._execucao(linha)
        execucao["erros"] = [dict(e) for e in erros]
        return execucao

    @staticmethod
    def _execucao(linha: sqlite3.Row) -> dict:
        dados = dict(linha)
        dados["resumo"] = json.loads(dados["resumo"]) if dados.get("resumo") else None
        return dados
