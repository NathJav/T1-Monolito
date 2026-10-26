"""Leitura do i-Educar pela API REST autenticada (Laravel Sanctum).

Endpoints usados (rotas `routes/api.php` do i-Educar, middleware `auth:sanctum`):

    GET /api/school         escolas          include=person:nome
    GET /api/school-class   turmas           year_eq=<ano>, include=grade|course
    GET /api/registration   matrículas       year_eq=<ano>, include=enrollments
    GET /api/version        verificação de disponibilidade (pública)

Todos são paginados (`show`/`page`) e devolvem {"data": [...], "meta": {...}}.
O token é gerado no i-Educar com `php artisan admin:token <nome>`.

Observação sobre /api/registration: o endpoint faz JOIN com `pmieducar.aluno` e
`cadastro.pessoa` para ordenar por nome; por isso a coluna `ativo` fica ambígua.
Pedimos as colunas explicitamente com o nome qualificado (`only=`), o que também
traz `nome` e `email` do aluno na mesma consulta.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import httpx

from app.domain import Aluno, DadosIEducar, Escola, Matricula, Turma
from app.errors import ConfiguracaoError, IEducarError

logger = logging.getLogger(__name__)

COLUNAS_MATRICULA = ",".join(
    [
        "pmieducar.matricula.ativo",
        "pmieducar.matricula.ano",
        "pmieducar.matricula.aprovado",
        "pmieducar.matricula.ref_ref_cod_escola",
        "ref_cod_aluno",
        "nome",
        "email",
    ]
)


def _int(valor: Any, padrao: int | None = None) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


def _ativo(valor: Any) -> bool:
    if isinstance(valor, bool):
        return valor
    return _int(valor, 1) == 1


def _nome_relacao(registro: dict, relacao: str, *campos: str) -> str | None:
    dados = registro.get(relacao)
    if isinstance(dados, dict):
        for campo in ("name", *campos):
            if dados.get(campo):
                return str(dados[campo])
    return None


class IEducarApiSource:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        http: httpx.Client | None = None,
        tamanho_pagina: int = 100,
        tentativas: int = 3,
        timeout: float = 30.0,
        espera_base: float = 0.5,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http or httpx.Client(timeout=timeout)
        self.tamanho_pagina = tamanho_pagina
        self.tentativas = max(1, tentativas)
        self.espera_base = espera_base

    # ------------------------------------------------------------------ HTTP
    def _get(self, caminho: str, params: dict | None = None, autenticado: bool = True) -> dict:
        if autenticado and not self.token:
            raise ConfiguracaoError("Token do i-Educar não configurado (IEDUCAR_TOKEN ou IEDUCAR_TOKEN_FILE).")
        headers = {"Accept": "application/json"}
        if autenticado:
            headers["Authorization"] = f"Bearer {self.token}"
        url = f"{self.base_url}{caminho}"

        for tentativa in range(1, self.tentativas + 1):
            try:
                resposta = self.http.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                if tentativa == self.tentativas:
                    raise IEducarError(f"i-Educar indisponível em {caminho}: {exc}") from exc
                self._esperar(tentativa, None, caminho, exc)
                continue

            if resposta.status_code == 429 or resposta.status_code >= 500:
                if tentativa == self.tentativas:
                    raise IEducarError(f"i-Educar respondeu HTTP {resposta.status_code} em {caminho}")
                self._esperar(tentativa, resposta.headers.get("Retry-After"), caminho, resposta.status_code)
                continue
            if resposta.status_code in (401, 403):
                raise IEducarError(f"Token do i-Educar recusado (HTTP {resposta.status_code}).")
            if resposta.status_code >= 400:
                raise IEducarError(f"i-Educar respondeu HTTP {resposta.status_code} em {caminho}: {resposta.text[:200]}")
            try:
                return resposta.json()
            except ValueError as exc:
                raise IEducarError(f"Resposta não-JSON do i-Educar em {caminho}") from exc
        raise IEducarError(f"Falha ao consultar {caminho}")  # pragma: no cover

    def _esperar(self, tentativa: int, retry_after: str | None, caminho: str, motivo: Any) -> None:
        espera = _int(retry_after) or self.espera_base * 2 ** (tentativa - 1)
        logger.warning("i-Educar %s falhou (%s); nova tentativa em %.1fs", caminho, motivo, espera)
        time.sleep(espera)

    def _paginas(self, caminho: str, params: dict) -> Iterator[dict]:
        pagina = 1
        while True:
            corpo = self._get(caminho, {**params, "show": self.tamanho_pagina, "page": pagina})
            yield from corpo.get("data", [])
            meta = corpo.get("meta") or {}
            ultima = _int(meta.get("last_page"), pagina) or pagina
            if pagina >= ultima or not corpo.get("data"):
                break
            pagina += 1

    # ------------------------------------------------------------ contrato
    def verificar(self) -> dict:
        versao = self._get("/api/version", autenticado=False)
        # Confirma também que o token é aceito.
        self._get("/api/school", {"show": 1})
        return {"versao": versao.get("version"), "build": versao.get("build")}

    def carregar(self, ano: int) -> DadosIEducar:
        escolas = self.listar_escolas()
        turmas = self.listar_turmas(ano)
        alunos, matriculas = self.listar_matriculas(ano)
        ids_turmas = {t.id for t in turmas}
        ids_escolas = {e.id for e in escolas}
        # Considera apenas o que pertence às turmas/escolas lidas.
        turmas = [t for t in turmas if t.escola_id in ids_escolas]
        matriculas = [m for m in matriculas if m.turma_id in ids_turmas]
        ids_alunos = {m.aluno_id for m in matriculas}
        alunos = [a for a in alunos if a.id in ids_alunos]
        logger.info(
            "i-Educar: %d escolas, %d turmas, %d alunos, %d matrículas (ano %d)",
            len(escolas), len(turmas), len(alunos), len(matriculas), ano,
        )
        return DadosIEducar(escolas=escolas, turmas=turmas, alunos=alunos, matriculas=matriculas)

    # ----------------------------------------------------------- recursos
    def listar_escolas(self) -> list[Escola]:
        escolas = []
        for r in self._paginas("/api/school", {"include": "person:nome"}):
            escola_id = _int(r.get("id") or r.get("cod_escola"))
            if escola_id is None:
                continue
            nome = _nome_relacao(r, "person", "nome") or r.get("fantasia") or f"Escola {escola_id}"
            escolas.append(Escola(id=escola_id, nome=nome, ativo=_ativo(r.get("ativo"))))
        return escolas

    def listar_turmas(self, ano: int) -> list[Turma]:
        turmas = []
        params = {"year_eq": ano, "include": "grade:nm_serie|course:nm_curso"}
        for r in self._paginas("/api/school-class", params):
            turma_id = _int(r.get("cod_turma") or r.get("id"))
            escola_id = _int(r.get("ref_ref_cod_escola") or r.get("school_id"))
            if turma_id is None or escola_id is None or _int(r.get("ano")) != ano:
                continue
            turmas.append(
                Turma(
                    id=turma_id,
                    nome=str(r.get("nm_turma") or f"Turma {turma_id}"),
                    ano=ano,
                    escola_id=escola_id,
                    ativo=_ativo(r.get("ativo")) and r.get("visivel") is not False,
                    serie=_nome_relacao(r, "grade", "nm_serie"),
                    curso=_nome_relacao(r, "course", "nm_curso"),
                )
            )
        return turmas

    def listar_matriculas(self, ano: int) -> tuple[list[Aluno], list[Matricula]]:
        alunos: dict[int, Aluno] = {}
        matriculas: list[Matricula] = []
        params = {
            "year_eq": ano,
            "only": COLUNAS_MATRICULA,
            "include": "enrollments:ref_cod_turma,sequencial,ativo",
        }
        for r in self._paginas("/api/registration", params):
            matricula_id = _int(r.get("id") or r.get("cod_matricula"))
            aluno_id = _int(r.get("student_id") or r.get("ref_cod_aluno"))
            if matricula_id is None or aluno_id is None:
                continue
            enturmacao = self._enturmacao_vigente(r.get("enrollments") or [])
            if enturmacao is None:
                # Matrícula ainda não enturmada: não há curso correspondente no Moodle.
                continue
            alunos.setdefault(
                aluno_id,
                Aluno(id=aluno_id, nome=str(r.get("nome") or f"Aluno {aluno_id}"), email=r.get("email") or None),
            )
            matriculas.append(
                Matricula(
                    id=matricula_id,
                    aluno_id=aluno_id,
                    turma_id=_int(enturmacao["ref_cod_turma"]),
                    ano=_int(r.get("ano"), ano),
                    situacao=_int(r.get("aprovado")),
                    ativa=_ativo(r.get("ativo")) and _ativo(enturmacao.get("ativo")),
                )
            )
        return list(alunos.values()), matriculas

    @staticmethod
    def _enturmacao_vigente(enturmacoes: list[dict]) -> dict | None:
        """Enturmação ativa mais recente; se não houver, a última registrada."""
        validas = [e for e in enturmacoes if _int(e.get("ref_cod_turma")) is not None]
        if not validas:
            return None
        ordenadas = sorted(validas, key=lambda e: (_ativo(e.get("ativo")), _int(e.get("sequencial"), 0)))
        return ordenadas[-1]
