"""Regras de sincronização i-Educar -> Moodle.

Ordem de execução (respeita as dependências entre os registros):
    1. Escolas    -> categorias
    2. Turmas     -> cursos (dentro da categoria da escola)
    3. Alunos     -> usuários
    4. Matrículas -> inscrições no curso da turma

Garantias:
  * Idempotência: cada registro é procurado pelo vínculo local e, na falta dele,
    pelo `idnumber` no Moodle antes de ser criado. Rodar duas vezes não duplica nada.
  * Envio só do que mudou: o hash dos dados de origem fica salvo no vínculo.
  * Isolamento de falhas: o erro em um registro é registrado e a execução continua;
    registros dependentes daquele são pulados com o motivo.
  * Exclusão mútua: só uma sincronização roda por vez.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from app.domain import Aluno, DadosIEducar, Escola, Matricula, Turma
from app.errors import (
    DependenciaNaoSincronizada,
    MoodleError,
    MoodleIndisponivel,
    SincronizacaoEmAndamento,
)
from app.ieducar.base import FonteIEducar
from app.moodle.client import MoodleClient
from app.storage import Repositorio
from app.sync import mapper

logger = logging.getLogger(__name__)

ENTIDADES = ("escolas", "turmas", "alunos", "matriculas")
LIMITE_ERROS_RELATORIO = 100


@dataclass
class Contador:
    criados: int = 0
    atualizados: int = 0
    vinculados: int = 0
    inalterados: int = 0
    suspensos: int = 0
    erros: int = 0

    def registrar(self, acao: str) -> None:
        setattr(self, acao, getattr(self, acao) + 1)


@dataclass
class Relatorio:
    execucao_id: int
    ano: int
    gatilho: str
    inicio: str
    fim: str | None = None
    status: str = "em_andamento"
    origem: dict = field(default_factory=dict)
    entidades: dict[str, Contador] = field(default_factory=lambda: {e: Contador() for e in ENTIDADES})
    erros: list[dict] = field(default_factory=list)

    def como_dict(self) -> dict:
        return {
            "execucao_id": self.execucao_id,
            "status": self.status,
            "ano": self.ano,
            "gatilho": self.gatilho,
            "inicio": self.inicio,
            "fim": self.fim,
            "origem": self.origem,
            "entidades": {nome: vars(c) for nome, c in self.entidades.items()},
            "total_erros": len(self.erros),
            "erros": self.erros[:LIMITE_ERROS_RELATORIO],
        }


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SyncService:
    def __init__(
        self,
        fonte: FonteIEducar,
        moodle: MoodleClient,
        repositorio: Repositorio,
        papel_estudante_id: int = 5,
        criar_senha: bool = True,
        dominio_email_placeholder: str = "sem-email.invalid",
    ):
        self.fonte = fonte
        self.moodle = moodle
        self.repo = repositorio
        self.papel_estudante_id = papel_estudante_id
        self.criar_senha = criar_senha
        self.dominio_email_placeholder = dominio_email_placeholder
        self._trava = threading.Lock()
        self._completa = False

    @property
    def em_andamento(self) -> bool:
        return self._trava.locked()

    # ================================================================ execução
    def executar(self, ano: int, gatilho: str = "manual", completa: bool = False) -> dict:
        """Executa a sincronização.

        `completa=True` ignora os hashes salvos e confere cada registro no Moodle,
        recriando o que tiver sido apagado lá manualmente (reconciliação).
        """
        if not self._trava.acquire(blocking=False):
            raise SincronizacaoEmAndamento("Já existe uma sincronização em andamento.")
        self._completa = completa
        try:
            return self._executar(ano, gatilho + (" (completa)" if completa else ""))
        finally:
            self._completa = False
            self._trava.release()

    def _executar(self, ano: int, gatilho: str) -> dict:
        execucao_id = self.repo.iniciar_execucao(gatilho, ano)
        relatorio = Relatorio(execucao_id=execucao_id, ano=ano, gatilho=gatilho, inicio=_agora())
        logger.info("Sincronização #%s iniciada (ano %s, %s)", execucao_id, ano, gatilho)

        try:
            dados = self.fonte.carregar(ano)
        except Exception as exc:  # noqa: BLE001 - qualquer falha de leitura aborta a execução
            logger.exception("Falha ao ler dados do i-Educar")
            self._erro(relatorio, "ieducar", None, f"Falha ao ler o i-Educar: {exc}")
            relatorio.status = "falha"
            return self._finalizar(relatorio)

        try:
            self.moodle.info_site()
        except Exception as exc:  # noqa: BLE001
            self._erro(relatorio, "moodle", None, str(exc))
            relatorio.status = "falha"
            return self._finalizar(relatorio)

        relatorio.origem = {
            "escolas": len(dados.escolas),
            "turmas": len(dados.turmas),
            "alunos": len(dados.alunos),
            "matriculas": len(dados.matriculas),
        }

        try:
            categorias = self._sincronizar_escolas(dados.escolas, relatorio)
            cursos = self._sincronizar_turmas(dados, categorias, relatorio)
            usuarios = self._sincronizar_alunos(dados.alunos, relatorio)
            self._sincronizar_matriculas(dados.matriculas, usuarios, cursos, ano, relatorio)
        except MoodleIndisponivel as exc:
            self._erro(relatorio, "moodle", None, f"Execução interrompida: {exc}")
            relatorio.status = "falha"
            return self._finalizar(relatorio)
        except Exception as exc:  # noqa: BLE001 - garante o registro da execução
            logger.exception("Falha inesperada na sincronização")
            self._erro(relatorio, "integracao", None, f"Falha inesperada: {exc}")
            relatorio.status = "falha"
            return self._finalizar(relatorio)

        total_erros = sum(c.erros for c in relatorio.entidades.values())
        relatorio.status = "sucesso" if total_erros == 0 else "parcial"
        return self._finalizar(relatorio)

    def _finalizar(self, relatorio: Relatorio) -> dict:
        relatorio.fim = _agora()
        resultado = relatorio.como_dict()
        self.repo.finalizar_execucao(relatorio.execucao_id, relatorio.status, resultado)
        logger.info("Sincronização #%s finalizada: %s", relatorio.execucao_id, relatorio.status)
        return resultado

    def _erro(self, relatorio: Relatorio, entidade: str, id_origem, mensagem: str) -> None:
        if entidade in relatorio.entidades:
            relatorio.entidades[entidade].erros += 1
        relatorio.erros.append({"entidade": entidade, "id_origem": id_origem, "mensagem": mensagem})
        self.repo.registrar_erro(
            relatorio.execucao_id, entidade, None if id_origem is None else str(id_origem), mensagem
        )
        logger.warning("[%s %s] %s", entidade, id_origem, mensagem)

    # ================================================================ upsert
    def _upsert(
        self,
        tipo: str,
        id_origem: int,
        hash_origem: str,
        idnumber: str,
        buscar: Callable[[str], dict | None],
        criar: Callable[[], int],
        atualizar: Callable[[int], None],
    ) -> tuple[int, str]:
        """Cria ou atualiza um registro no Moodle sem gerar duplicatas.

        Devolve (id_no_moodle, ação), onde ação é criados/atualizados/vinculados/inalterados.
        """
        vinculo = self.repo.obter_vinculo(tipo, id_origem)
        if vinculo and vinculo.hash == hash_origem and not self._completa:
            return vinculo.id_moodle, "inalterados"

        moodle_id = vinculo.id_moodle if vinculo else None
        acao = "atualizados"
        if moodle_id is None:
            existente = buscar(idnumber)
            if existente:
                moodle_id = int(existente["id"])
                acao = "vinculados"

        if moodle_id is not None:
            try:
                atualizar(moodle_id)
            except MoodleError as exc:
                if not exc.registro_inexistente:
                    raise
                # O registro foi apagado no Moodle: descarta o vínculo e recria.
                logger.info("%s %s não existe mais no Moodle; recriando", tipo, id_origem)
                self.repo.remover_vinculo(tipo, id_origem)
                moodle_id = None

        if moodle_id is None:
            moodle_id = criar()
            acao = "criados"

        self.repo.salvar_vinculo(tipo, id_origem, moodle_id, hash_origem)
        return moodle_id, acao

    # ================================================================ escolas
    def _sincronizar_escolas(self, escolas: list[Escola], relatorio: Relatorio) -> dict[int, int]:
        contador = relatorio.entidades["escolas"]
        categorias: dict[int, int] = {}
        for escola in escolas:
            try:
                payload = mapper.categoria(escola)
                moodle_id, acao = self._upsert(
                    "escola",
                    escola.id,
                    mapper.hash_payload(payload),
                    payload["idnumber"],
                    buscar=self.moodle.buscar_categoria,
                    criar=lambda p=payload: self.moodle.criar_categoria({**p, "parent": 0}),
                    atualizar=lambda mid, p=payload: self.moodle.atualizar_categoria(mid, p),
                )
                categorias[escola.id] = moodle_id
                contador.registrar(acao)
            except MoodleIndisponivel:
                raise
            except Exception as exc:  # noqa: BLE001 - isola a falha no registro
                self._erro(relatorio, "escolas", escola.id, str(exc))
        return categorias

    # ================================================================ turmas
    def _sincronizar_turmas(
        self, dados: DadosIEducar, categorias: dict[int, int], relatorio: Relatorio
    ) -> dict[int, int]:
        contador = relatorio.entidades["turmas"]
        nomes_escolas = {e.id: e.nome for e in dados.escolas}
        cursos: dict[int, int] = {}
        for turma in dados.turmas:
            try:
                categoria_id = categorias.get(turma.escola_id)
                if categoria_id is None:
                    raise DependenciaNaoSincronizada(
                        f"Escola {turma.escola_id} da turma não foi sincronizada."
                    )
                payload = mapper.curso(turma, categoria_id, nomes_escolas.get(turma.escola_id))
                moodle_id, acao = self._upsert(
                    "turma",
                    turma.id,
                    mapper.hash_payload(payload),
                    payload["idnumber"],
                    buscar=self.moodle.buscar_curso,
                    criar=lambda p=payload: self.moodle.criar_curso(p),
                    atualizar=lambda mid, p=payload: self.moodle.atualizar_curso(mid, p),
                )
                cursos[turma.id] = moodle_id
                contador.registrar(acao)
            except MoodleIndisponivel:
                raise
            except Exception as exc:  # noqa: BLE001 - isola a falha no registro
                self._erro(relatorio, "turmas", turma.id, str(exc))
        return cursos

    # ================================================================ alunos
    def _email_para(self, aluno: Aluno) -> str:
        """E-mail do aluno, ou um e-mail técnico se estiver ausente, inválido ou já em uso."""
        placeholder = mapper.email_placeholder(aluno.id, self.dominio_email_placeholder)
        if not mapper.email_valido(aluno.email):
            return placeholder
        email = aluno.email.strip().lower()
        meu_idnumber = mapper.idnumber("aluno", aluno.id)
        donos = self.moodle.buscar_usuarios("email", [email])
        if any(u.get("idnumber") != meu_idnumber for u in donos):
            # O Moodle não aceita duas contas com o mesmo e-mail (ex.: irmãos com o
            # e-mail do responsável). Mantém o aluno com um e-mail técnico.
            return placeholder
        return email

    def _sincronizar_alunos(self, alunos: list[Aluno], relatorio: Relatorio) -> dict[int, int]:
        contador = relatorio.entidades["alunos"]
        usuarios: dict[int, int] = {}
        for aluno in alunos:
            try:
                hash_origem = mapper.hash_payload({"nome": aluno.nome, "email": aluno.email})
                estado: dict = {}

                def email() -> str:
                    if "email" not in estado:
                        estado["email"] = self._email_para(aluno)
                    return estado["email"]

                moodle_id, acao = self._upsert(
                    "aluno",
                    aluno.id,
                    hash_origem,
                    mapper.idnumber("aluno", aluno.id),
                    buscar=lambda idn: self.moodle.buscar_usuario("idnumber", idn)
                    or self.moodle.buscar_usuario("username", mapper.username_aluno(aluno.id)),
                    criar=lambda: self.moodle.criar_usuario(
                        mapper.novo_usuario(aluno, email(), self.criar_senha, self._senha_inicial())
                    ),
                    atualizar=lambda mid: self.moodle.atualizar_usuario(mid, mapper.usuario(aluno, email())),
                )
                usuarios[aluno.id] = moodle_id
                contador.registrar(acao)
            except MoodleIndisponivel:
                raise
            except Exception as exc:  # noqa: BLE001 - isola a falha no registro
                self._erro(relatorio, "alunos", aluno.id, str(exc))
        return usuarios

    @staticmethod
    def _senha_inicial() -> str:
        import secrets

        return "Ie@" + secrets.token_urlsafe(12)

    # ============================================================= matrículas
    def _sincronizar_matriculas(
        self,
        matriculas: list[Matricula],
        usuarios: dict[int, int],
        cursos: dict[int, int],
        ano: int,
        relatorio: Relatorio,
    ) -> None:
        contador = relatorio.entidades["matriculas"]
        # Processa primeiro as inativas: se o aluno saiu e voltou para a mesma turma,
        # a inscrição ativa é a que prevalece.
        ordenadas = sorted(matriculas, key=lambda m: (m.deve_ter_acesso, m.id))
        vistos: set[str] = set()

        for matricula in ordenadas:
            vistos.add(str(matricula.id))
            try:
                usuario_id = usuarios.get(matricula.aluno_id)
                curso_id = cursos.get(matricula.turma_id)
                if usuario_id is None:
                    raise DependenciaNaoSincronizada(f"Aluno {matricula.aluno_id} não foi sincronizado.")
                if curso_id is None:
                    raise DependenciaNaoSincronizada(f"Turma {matricula.turma_id} não foi sincronizada.")

                acesso = matricula.deve_ter_acesso
                hash_origem = mapper.hash_payload(
                    {"usuario": usuario_id, "curso": curso_id, "acesso": acesso}
                )
                vinculo = self.repo.obter_vinculo("matricula", matricula.id)
                if vinculo and vinculo.hash == hash_origem and not self._completa:
                    contador.registrar("inalterados")
                    continue

                # Troca de turma (remanejamento): suspende a inscrição no curso antigo.
                turma_anterior = vinculo.extra.get("turma") if vinculo else None
                if turma_anterior not in (None, matricula.turma_id):
                    self._suspender_inscricao_antiga(vinculo.extra, matricula.id)

                self.moodle.inscrever(usuario_id, curso_id, self.papel_estudante_id, suspenso=not acesso)
                self.repo.salvar_vinculo(
                    "matricula",
                    matricula.id,
                    curso_id,
                    hash_origem,
                    {
                        "usuario": usuario_id,
                        "curso": curso_id,
                        "turma": matricula.turma_id,
                        "suspenso": not acesso,
                        "ano": ano,
                    },
                )
                if not acesso:
                    contador.registrar("suspensos")
                elif vinculo:
                    contador.registrar("atualizados")
                else:
                    contador.registrar("criados")
            except MoodleIndisponivel:
                raise
            except Exception as exc:  # noqa: BLE001 - isola a falha no registro
                self._erro(relatorio, "matriculas", matricula.id, str(exc))

        self._suspender_orfas(vistos, ano, relatorio)

    def _suspender_inscricao_antiga(self, extra: dict, matricula_id: int) -> None:
        try:
            self.moodle.inscrever(extra["usuario"], extra["curso"], self.papel_estudante_id, suspenso=True)
        except MoodleIndisponivel:
            raise
        except MoodleError as exc:
            # O curso antigo pode ter sido apagado no Moodle; não impede a nova inscrição.
            logger.warning("Matrícula %s: não foi possível suspender no curso antigo: %s", matricula_id, exc)

    def _suspender_orfas(self, vistos: set[str], ano: int, relatorio: Relatorio) -> None:
        """Matrículas sincronizadas antes que sumiram do i-Educar perdem o acesso."""
        contador = relatorio.entidades["matriculas"]
        for vinculo in self.repo.listar_vinculos("matricula", limite=1_000_000):
            extra = vinculo.extra
            if vinculo.id_origem in vistos or extra.get("ano") != ano or extra.get("suspenso"):
                continue
            try:
                self.moodle.inscrever(extra["usuario"], extra["curso"], self.papel_estudante_id, suspenso=True)
                self.repo.salvar_vinculo(
                    "matricula",
                    vinculo.id_origem,
                    vinculo.id_moodle,
                    "removida-na-origem",
                    {**extra, "suspenso": True},
                )
                contador.registrar("suspensos")
            except MoodleIndisponivel:
                raise
            except Exception as exc:  # noqa: BLE001 - isola a falha no registro
                self._erro(relatorio, "matriculas", vinculo.id_origem, f"Falha ao suspender: {exc}")
