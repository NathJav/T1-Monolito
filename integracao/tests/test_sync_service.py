"""Regras de sincronização: criação, idempotência, atualização, duplicidade e falhas."""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from app.domain import Aluno, Matricula, SituacaoMatricula, Turma
from app.errors import IEducarError, MoodleError, SincronizacaoEmAndamento
from app.storage import Repositorio
from app.sync.service import SyncService
from conftest import ANO
from fakes import FakeMoodle


def resumo(relatorio: dict) -> dict:
    return {nome: {k: v for k, v in c.items() if v} for nome, c in relatorio["entidades"].items()}


# ------------------------------------------------------------------ criação
def test_primeira_execucao_cria_tudo(servico, moodle):
    r = servico.executar(ANO)

    assert r["status"] == "sucesso"
    assert resumo(r) == {
        "escolas": {"criados": 2},
        "turmas": {"criados": 3},
        "alunos": {"criados": 3},
        "matriculas": {"criados": 3},
    }
    assert len(moodle.categorias) == 2
    assert len(moodle.cursos) == 3
    assert len(moodle.usuarios) == 3
    assert all(not i["suspenso"] and i["papel"] == 5 for i in moodle.inscricoes.values())


def test_curso_fica_na_categoria_da_escola(servico, moodle):
    servico.executar(ANO)
    categoria = next(c for c in moodle.categorias.values() if c["idnumber"] == "ieducar-escola-2")
    curso = next(c for c in moodle.cursos.values() if c["idnumber"] == "ieducar-turma-20")
    assert curso["categoryid"] == categoria["id"]


def test_alunos_sem_email_valido_recebem_email_tecnico(servico, moodle):
    servico.executar(ANO)
    emails = {u["username"]: u["email"] for u in moodle.usuarios.values()}
    assert emails == {
        "aluno100": "maria@exemplo.com",
        "aluno101": "aluno101@sem-email.invalid",
        "aluno102": "aluno102@sem-email.invalid",
    }


# ----------------------------------------------------- idempotência / hash
def test_segunda_execucao_nao_escreve_nada(servico, moodle):
    servico.executar(ANO)
    escritas = moodle.contar_escritas()

    r = servico.executar(ANO)

    assert moodle.contar_escritas() == escritas
    assert resumo(r) == {
        "escolas": {"inalterados": 2},
        "turmas": {"inalterados": 3},
        "alunos": {"inalterados": 3},
        "matriculas": {"inalterados": 3},
    }


def test_perda_do_banco_local_nao_duplica_no_moodle(fonte, moodle):
    SyncService(fonte, moodle, Repositorio(":memory:")).executar(ANO)

    # Novo banco local vazio: os registros são reencontrados pelo idnumber.
    r = SyncService(fonte, moodle, Repositorio(":memory:")).executar(ANO)

    assert r["status"] == "sucesso"
    assert r["entidades"]["escolas"]["vinculados"] == 2
    assert r["entidades"]["turmas"]["vinculados"] == 3
    assert r["entidades"]["alunos"]["vinculados"] == 3
    assert (len(moodle.categorias), len(moodle.cursos), len(moodle.usuarios)) == (2, 3, 3)
    assert len(moodle.inscricoes) == 3


def test_reconciliacao_completa_recria_curso_apagado_no_moodle(servico, moodle):
    servico.executar(ANO)
    apagado = next(i for i, c in moodle.cursos.items() if c["idnumber"] == "ieducar-turma-10")
    del moodle.cursos[apagado]
    moodle.inscricoes = {k: v for k, v in moodle.inscricoes.items() if k[1] != apagado}

    # A execução normal confia no cache de hashes...
    assert servico.executar(ANO)["entidades"]["turmas"]["criados"] == 0
    # ...e a completa confere tudo no Moodle.
    r = servico.executar(ANO, completa=True)

    assert r["status"] == "sucesso"
    assert r["entidades"]["turmas"]["criados"] == 1
    novo = next(i for i, c in moodle.cursos.items() if c["idnumber"] == "ieducar-turma-10")
    assert {k for k in moodle.inscricoes if k[1] == novo} and len(moodle.inscricoes) == 3


# ------------------------------------------------------------- atualização
def test_alteracao_de_nome_atualiza_usuario(servico, fonte, moodle):
    servico.executar(ANO)
    fonte.dados.alunos[0] = replace(fonte.dados.alunos[0], nome="MARIA DA SILVA SOUZA")

    r = servico.executar(ANO)

    assert r["entidades"]["alunos"] == {**r["entidades"]["alunos"], "atualizados": 1, "inalterados": 2}
    usuario = next(u for u in moodle.usuarios.values() if u["username"] == "aluno100")
    assert (usuario["firstname"], usuario["lastname"]) == ("Maria", "da Silva Souza")


def test_email_repetido_entre_alunos_nao_quebra_sincronizacao(servico, fonte, moodle):
    # Irmãos cadastrados com o e-mail do responsável.
    fonte.dados.alunos[1] = replace(fonte.dados.alunos[1], email="maria@exemplo.com")

    r = servico.executar(ANO)

    assert r["status"] == "sucesso"
    emails = {u["username"]: u["email"] for u in moodle.usuarios.values()}
    assert emails["aluno100"] == "maria@exemplo.com"
    assert emails["aluno101"] == "aluno101@sem-email.invalid"


# --------------------------------------------------------------- matrículas
def test_transferencia_suspende_inscricao(servico, fonte, moodle):
    servico.executar(ANO)
    fonte.dados.matriculas[0] = replace(fonte.dados.matriculas[0], situacao=SituacaoMatricula.TRANSFERIDO)

    r = servico.executar(ANO)

    assert r["entidades"]["matriculas"]["suspensos"] == 1
    usuario = next(u["id"] for u in moodle.usuarios.values() if u["username"] == "aluno100")
    curso = next(c["id"] for c in moodle.cursos.values() if c["idnumber"] == "ieducar-turma-10")
    assert moodle.inscricoes[(usuario, curso)]["suspenso"] is True


def test_remanejamento_move_aluno_de_curso(servico, fonte, moodle):
    servico.executar(ANO)
    fonte.dados.matriculas[0] = replace(fonte.dados.matriculas[0], turma_id=11)

    r = servico.executar(ANO)

    assert r["status"] == "sucesso"
    usuario = next(u["id"] for u in moodle.usuarios.values() if u["username"] == "aluno100")
    curso_a = next(c["id"] for c in moodle.cursos.values() if c["idnumber"] == "ieducar-turma-10")
    curso_b = next(c["id"] for c in moodle.cursos.values() if c["idnumber"] == "ieducar-turma-11")
    assert moodle.inscricoes[(usuario, curso_a)]["suspenso"] is True
    assert moodle.inscricoes[(usuario, curso_b)]["suspenso"] is False


def test_matricula_removida_na_origem_e_suspensa(servico, fonte, moodle):
    servico.executar(ANO)
    fonte.dados.matriculas.pop(2)

    r = servico.executar(ANO)

    assert r["entidades"]["matriculas"]["suspensos"] == 1
    assert sum(1 for i in moodle.inscricoes.values() if i["suspenso"]) == 1


def test_saida_e_retorno_na_mesma_turma_mantem_acesso(servico, fonte, moodle):
    fonte.dados.matriculas.append(Matricula(999, 100, 10, ANO, SituacaoMatricula.TRANSFERIDO, ativa=False))

    servico.executar(ANO)

    usuario = next(u["id"] for u in moodle.usuarios.values() if u["username"] == "aluno100")
    curso = next(c["id"] for c in moodle.cursos.values() if c["idnumber"] == "ieducar-turma-10")
    assert moodle.inscricoes[(usuario, curso)]["suspenso"] is False


# ------------------------------------------------------------------ falhas
def test_falha_em_um_registro_nao_interrompe_os_demais(servico, moodle):
    moodle.falhar_em["criar_curso:ieducar-turma-10"] = MoodleError("Erro simulado", codigo="x")

    r = servico.executar(ANO)

    assert r["status"] == "parcial"
    assert r["entidades"]["turmas"] == {**r["entidades"]["turmas"], "criados": 2, "erros": 1}
    # As duas matrículas da turma que falhou são puladas com o motivo.
    assert r["entidades"]["matriculas"]["erros"] == 2
    assert any("Turma 10 não foi sincronizada" in e["mensagem"] for e in r["erros"])
    assert r["entidades"]["matriculas"]["criados"] == 1


def test_registro_que_falhou_e_refeito_na_execucao_seguinte(servico, moodle):
    moodle.falhar_em["criar_curso:ieducar-turma-10"] = MoodleError("Erro simulado", codigo="x")
    servico.executar(ANO)
    moodle.falhar_em.clear()

    r = servico.executar(ANO)

    assert r["status"] == "sucesso"
    assert r["entidades"]["turmas"]["criados"] == 1
    assert r["entidades"]["matriculas"]["criados"] == 2
    assert len(moodle.inscricoes) == 3


def test_moodle_fora_do_ar_aborta_sem_tentar_cada_registro(servico, moodle):
    moodle.fora_do_ar = True

    r = servico.executar(ANO)

    assert r["status"] == "falha"
    assert r["erros"][0]["entidade"] == "moodle"
    assert r["total_erros"] == 1
    assert moodle.chamadas == []


def test_moodle_cai_no_meio_da_execucao(servico, moodle):
    original = moodle.criar_usuario

    def cair(dados):
        moodle.fora_do_ar = True
        return original(dados)

    moodle.criar_usuario = cair
    r = servico.executar(ANO)

    assert r["status"] == "falha"
    assert "interrompida" in r["erros"][-1]["mensagem"]


def test_ieducar_indisponivel_registra_falha(servico, fonte, repo):
    fonte.erro = IEducarError("i-Educar indisponível")

    r = servico.executar(ANO)

    assert r["status"] == "falha"
    execucao = repo.obter_execucao(r["execucao_id"])
    assert execucao["status"] == "falha"
    assert execucao["erros"][0]["entidade"] == "ieducar"


def test_turma_de_escola_desconhecida_vira_erro_de_dependencia(servico, fonte):
    fonte.dados.turmas.append(Turma(99, "Turma órfã", ANO, escola_id=404))

    r = servico.executar(ANO)

    assert r["entidades"]["turmas"]["erros"] == 1
    assert "Escola 404" in r["erros"][0]["mensagem"]


def test_execucoes_concorrentes_sao_bloqueadas(fonte, repo):
    liberar = threading.Event()
    entrou = threading.Event()

    class MoodleLento(FakeMoodle):
        def info_site(self):
            entrou.set()
            liberar.wait(5)
            return super().info_site()

    servico = SyncService(fonte, MoodleLento(), repo)
    t = threading.Thread(target=servico.executar, args=(ANO,))
    t.start()
    entrou.wait(5)
    with pytest.raises(SincronizacaoEmAndamento):
        servico.executar(ANO)
    liberar.set()
    t.join(5)


def test_historico_de_execucoes_e_persistido(servico, repo, moodle):
    moodle.falhar_em["criar_usuario:ieducar-aluno-101"] = MoodleError("Erro simulado")
    r = servico.executar(ANO)

    execucao = repo.obter_execucao(r["execucao_id"])
    assert execucao["status"] == "parcial"
    assert execucao["resumo"]["entidades"]["alunos"]["erros"] == 1
    assert execucao["erros"][0]["id_origem"] == "101"
    assert repo.contar_vinculos()["aluno"] == 2


def test_aluno_novo_na_origem_e_criado_na_execucao_seguinte(servico, fonte, moodle):
    servico.executar(ANO)
    fonte.dados.alunos.append(Aluno(103, "Pedro Alves", "pedro@exemplo.com"))
    fonte.dados.matriculas.append(Matricula(1003, 103, 20, ANO, SituacaoMatricula.CURSANDO))

    r = servico.executar(ANO)

    assert r["entidades"]["alunos"]["criados"] == 1
    assert r["entidades"]["matriculas"]["criados"] == 1
    assert len(moodle.inscricoes) == 4
