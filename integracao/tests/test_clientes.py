"""Clientes HTTP do Moodle e do i-Educar, com respostas simuladas por httpx.MockTransport.

As respostas do i-Educar em tests/fixtures foram capturadas de uma instância real
(i-Educar 2.12, seeder de demonstração) e reduzidas aos campos usados.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.errors import IEducarError, MoodleError, MoodleIndisponivel
from app.ieducar.api_source import IEducarApiSource
from app.moodle.client import MoodleClient, achatar_parametros

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(nome: str) -> dict:
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


# ===================================================================== Moodle
def moodle_com(handler) -> MoodleClient:
    return MoodleClient(
        "http://moodle", "tok", http=httpx.Client(transport=httpx.MockTransport(handler)), espera_base=0
    )


def test_parametros_no_formato_php():
    pares = achatar_parametros({"users": [{"username": "a", "createpassword": True}], "x": None})
    assert pares == [("users[0][username]", "a"), ("users[0][createpassword]", "1")]


def test_chamada_envia_token_funcao_e_formato_json():
    recebido = {}

    def handler(request: httpx.Request):
        recebido.update({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        recebido["path"] = request.url.path
        return httpx.Response(200, json=[{"id": 7, "username": "aluno1"}])

    assert moodle_com(handler).criar_usuario({"username": "aluno1", "email": "a@b.com"}) == 7
    assert recebido["path"] == "/webservice/rest/server.php"
    assert recebido["wstoken"] == "tok"
    assert recebido["wsfunction"] == "core_user_create_users"
    assert recebido["moodlewsrestformat"] == "json"
    assert recebido["users[0][email]"] == "a@b.com"


def test_excecao_do_moodle_vira_moodle_error_com_codigo():
    def handler(request):
        return httpx.Response(
            200,
            json={"exception": "dml_missing_record_exception", "errorcode": "invalidrecord", "message": "Não encontrado"},
        )

    with pytest.raises(MoodleError) as erro:
        moodle_com(handler).atualizar_curso(1, {"fullname": "x"})
    assert erro.value.codigo == "invalidrecord"
    assert erro.value.registro_inexistente


def test_avisos_na_atualizacao_viram_erro():
    def handler(request):
        return httpx.Response(200, json={"warnings": [{"warningcode": "errorcourseupdate", "message": "falhou"}]})

    with pytest.raises(MoodleError, match="falhou"):
        moodle_com(handler).atualizar_curso(1, {"fullname": "x"})


def test_erro_5xx_e_repetido_ate_dar_certo():
    respostas = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"sitename": "ok"})])
    assert moodle_com(lambda r: next(respostas)).info_site() == {"sitename": "ok"}


def test_falha_de_rede_persistente_vira_moodle_indisponivel():
    def handler(request):
        raise httpx.ConnectError("recusado", request=request)

    with pytest.raises(MoodleIndisponivel):
        moodle_com(handler).info_site()


def test_sem_token_nao_chama_o_moodle():
    cliente = MoodleClient("http://moodle", None, http=httpx.Client(transport=httpx.MockTransport(lambda r: 1 / 0)))
    with pytest.raises(Exception, match="Token do Moodle"):
        cliente.info_site()


# ==================================================================== i-Educar
def ieducar_com(handler, **kwargs) -> IEducarApiSource:
    return IEducarApiSource(
        "http://ieducar", "1|abc", http=httpx.Client(transport=httpx.MockTransport(handler)), espera_base=0, **kwargs
    )


def roteador_real(requisicoes: list | None = None):
    rotas = {
        "/api/school": fixture("ieducar_school.json"),
        "/api/school-class": fixture("ieducar_school_class.json"),
        "/api/registration": fixture("ieducar_registration.json"),
        "/api/version": {"entity": "i-Educar", "version": "2.12.0", "build": "abc"},
    }

    def handler(request: httpx.Request):
        if requisicoes is not None:
            requisicoes.append(request)
        return httpx.Response(200, json=rotas[request.url.path])

    return handler


def test_le_formato_real_da_api_do_ieducar():
    dados = ieducar_com(roteador_real()).carregar(2026)

    assert {e.nome for e in dados.escolas} == {"Escola de Educação Infantil", "Escola de Ensino Fundamental"}
    turma = dados.turmas[0]
    assert (turma.id, turma.ano, turma.escola_id, turma.serie) == (3, 2026, 2, "1º ano")
    # Apenas matrículas de turmas lidas entram (a fixture traz 2 turmas e 3 matrículas).
    ids_turmas = {t.id for t in dados.turmas}
    assert all(m.turma_id in ids_turmas for m in dados.matriculas)
    matricula = next(m for m in dados.matriculas if m.id == 10)
    assert (matricula.aluno_id, matricula.turma_id, matricula.situacao, matricula.ativa) == (10, 3, 3, True)
    aluno = next(a for a in dados.alunos if a.id == 10)
    assert aluno.nome == "Agatha Cristina Moraes" and aluno.email == "cesar01@yahoo.com"


def test_requisicoes_usam_token_filtros_e_colunas_qualificadas():
    requisicoes: list[httpx.Request] = []
    ieducar_com(roteador_real(requisicoes)).carregar(2026)

    for r in requisicoes:
        assert r.headers["Authorization"] == "Bearer 1|abc"
        assert r.headers["Accept"] == "application/json"
    matriculas = next(r for r in requisicoes if r.url.path == "/api/registration")
    assert matriculas.url.params["year_eq"] == "2026"
    assert "pmieducar.matricula.ativo" in matriculas.url.params["only"]
    assert matriculas.url.params["include"].startswith("enrollments")


def test_paginacao_percorre_todas_as_paginas():
    def handler(request: httpx.Request):
        pagina = int(request.url.params["page"])
        dados = [{"id": pagina, "ativo": 1, "person": {"name": f"Escola {pagina}", "id": pagina}}]
        return httpx.Response(200, json={"data": dados, "meta": {"current_page": pagina, "last_page": 3}})

    escolas = ieducar_com(handler, tamanho_pagina=1).listar_escolas()
    assert [e.id for e in escolas] == [1, 2, 3]


def test_enturmacao_vigente_prefere_a_ativa_mais_recente():
    escolhida = IEducarApiSource._enturmacao_vigente(
        [
            {"ref_cod_turma": 1, "sequencial": 1, "ativo": 0},
            {"ref_cod_turma": 2, "sequencial": 2, "ativo": 1},
            {"ref_cod_turma": 3, "sequencial": 3, "ativo": 0},
        ]
    )
    assert escolhida["ref_cod_turma"] == 2


def test_matricula_transferida_fica_sem_acesso():
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 5, "student_id": 9, "ano": 2026, "aprovado": 4, "ativo": 1, "nome": "X",
                        "enrollments": [{"ref_cod_turma": 1, "sequencial": 1, "ativo": 0}],
                    }
                ],
                "meta": {"last_page": 1},
            },
        )

    _, matriculas = ieducar_com(handler).listar_matriculas(2026)
    assert matriculas[0].ativa is False and matriculas[0].deve_ter_acesso is False


def test_token_recusado():
    with pytest.raises(IEducarError, match="recusado"):
        ieducar_com(lambda r: httpx.Response(401, json={"message": "Unauthenticated."})).listar_escolas()


def test_limite_de_requisicoes_429_e_repetido():
    respostas = iter(
        [
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"data": [], "meta": {"last_page": 1}}),
        ]
    )
    assert ieducar_com(lambda r: next(respostas)).listar_escolas() == []


def test_ieducar_fora_do_ar():
    def handler(request):
        raise httpx.ConnectError("recusado", request=request)

    with pytest.raises(IEducarError, match="indisponível"):
        ieducar_com(handler).listar_escolas()
