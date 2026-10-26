from app.domain import Aluno, Escola, Matricula, SituacaoMatricula, Turma
from app.sync import mapper


def test_idnumber_identifica_origem():
    assert mapper.idnumber("turma", 42) == "ieducar-turma-42"


def test_separar_nome():
    assert mapper.separar_nome("Maria da Silva") == ("Maria", "da Silva")
    assert mapper.separar_nome("  Ana  ") == ("Ana", "-")
    assert mapper.separar_nome("") == ("Sem nome", "(i-Educar)")


def test_nomes_em_caixa_alta_sao_normalizados():
    payload = mapper.categoria(Escola(1, "ESCOLA MUNICIPAL DE ENSINO FUNDAMENTAL DO CENTRO"))
    assert payload["name"] == "Escola Municipal de Ensino Fundamental do Centro"
    assert payload["idnumber"] == "ieducar-escola-1"


def test_payload_do_curso():
    turma = Turma(7, "TURMA A", 2026, 1, ativo=False, serie="5º ano", curso="Ensino Fundamental")
    payload = mapper.curso(turma, categoria_id=33, nome_escola="Escola X")
    assert payload["fullname"] == "Turma A — 5º ano · 2026"
    assert payload["shortname"] == "Turma A 2026 #7"
    assert payload["categoryid"] == 33
    assert payload["visible"] == 0
    assert "Escola X" in payload["summary"]


def test_payload_de_novo_usuario():
    aluno = Aluno(9, "JOÃO DOS SANTOS", "Joao@Exemplo.com")
    payload = mapper.novo_usuario(aluno, "joao@exemplo.com", criar_senha=True)
    assert payload == {
        "username": "aluno9",
        "auth": "manual",
        "firstname": "João",
        "lastname": "dos Santos",
        "email": "joao@exemplo.com",
        "idnumber": "ieducar-aluno-9",
        "createpassword": 1,
    }


def test_validacao_de_email():
    assert mapper.email_valido("a.b@escola.rs.gov.br")
    assert not mapper.email_valido("sem-arroba")
    assert not mapper.email_valido(None)


def test_hash_estavel_e_sensivel_a_mudanca():
    assert mapper.hash_payload({"a": 1, "b": 2}) == mapper.hash_payload({"b": 2, "a": 1})
    assert mapper.hash_payload({"a": 1}) != mapper.hash_payload({"a": 2})


def test_limite_de_tamanho_do_nome_do_curso():
    turma = Turma(1, "X" * 400, 2026, 1)
    assert len(mapper.curso(turma, 1)["fullname"]) <= 254


def test_regra_de_acesso_por_situacao():
    def m(situacao, ativa=True):
        return Matricula(1, 1, 1, 2026, situacao, ativa)

    assert m(SituacaoMatricula.CURSANDO).deve_ter_acesso
    assert m(SituacaoMatricula.EM_EXAME).deve_ter_acesso
    assert not m(SituacaoMatricula.TRANSFERIDO).deve_ter_acesso
    assert not m(SituacaoMatricula.ABANDONO).deve_ter_acesso
    assert not m(SituacaoMatricula.APROVADO).deve_ter_acesso
    assert not m(SituacaoMatricula.CURSANDO, ativa=False).deve_ter_acesso
    assert not m(999).deve_ter_acesso


def test_serie_nao_se_repete_no_nome_do_curso():
    turma = Turma(3, "1º ano (1º ano) Matutino", 2026, 1, serie="1º ano")
    assert mapper.curso(turma, 1)["fullname"] == "1º ano (1º ano) Matutino — 2026"
