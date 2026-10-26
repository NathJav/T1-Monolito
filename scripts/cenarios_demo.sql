-- Simula, no i-Educar, alterações que a secretaria faria pela interface, para
-- mostrar na demonstração como a integração reage. Não é necessário para o
-- funcionamento da integração.
--
-- Uso (com o ambiente no ar):
--   docker compose exec -T ieducar-db psql -U ieducar -d ieducar < scripts/cenarios_demo.sql
--
-- Escolhe as 5 primeiras matrículas ativas da primeira turma do ano letivo atual e:
--   1. corrige o nome do 1º aluno;
--   2. transfere a 2ª matrícula (situação 4 = transferido);
--   3. remaneja a 3ª matrícula para a turma seguinte;
--   4. faz o 5º aluno usar o mesmo e-mail do 4º (irmãos com o e-mail do responsável).
-- Depois, rode a sincronização e veja o resultado no Moodle.

DO $$
DECLARE
    ano_atual integer := extract(year FROM now())::integer;
    turma_a integer;
    turma_b integer;
    m integer[];
    a integer[];
BEGIN
    SELECT min(cod_turma) INTO turma_a
      FROM pmieducar.turma WHERE ano = ano_atual AND ativo = 1;
    SELECT min(cod_turma) INTO turma_b
      FROM pmieducar.turma WHERE ano = ano_atual AND ativo = 1 AND cod_turma > turma_a;

    SELECT array_agg(cod_matricula ORDER BY cod_matricula), array_agg(ref_cod_aluno ORDER BY cod_matricula)
      INTO m, a
      FROM (
        SELECT mat.cod_matricula, mat.ref_cod_aluno
          FROM pmieducar.matricula mat
          JOIN pmieducar.matricula_turma mt ON mt.ref_cod_matricula = mat.cod_matricula
         WHERE mt.ref_cod_turma = turma_a AND mt.ativo = 1 AND mat.ativo = 1 AND mat.aprovado = 3
         ORDER BY mat.cod_matricula
         LIMIT 5
      ) x;

    IF m IS NULL OR array_length(m, 1) < 5 OR turma_b IS NULL THEN
        RAISE EXCEPTION 'Dados insuficientes: carregue os dados de demonstração (IEDUCAR_DEMO_DATA=true).';
    END IF;

    -- 1. Correção de nome
    UPDATE cadastro.pessoa SET nome = nome || ' da Silva Demonstração'
     WHERE idpes = (SELECT ref_idpes FROM pmieducar.aluno WHERE cod_aluno = a[1]);

    -- 2. Transferência
    UPDATE pmieducar.matricula SET aprovado = 4 WHERE cod_matricula = m[2];
    UPDATE pmieducar.matricula_turma SET ativo = 0, transferido = true WHERE ref_cod_matricula = m[2];

    -- 3. Remanejamento para a turma seguinte
    UPDATE pmieducar.matricula_turma SET ativo = 0, remanejado = true WHERE ref_cod_matricula = m[3];
    INSERT INTO pmieducar.matricula_turma
        (ref_cod_matricula, ref_cod_turma, sequencial, ref_usuario_cad, data_cadastro, ativo, data_enturmacao)
    SELECT m[3], turma_b, coalesce(max(sequencial), 0) + 1, 1, now(), 1, now()
      FROM pmieducar.matricula_turma WHERE ref_cod_matricula = m[3];

    -- 4. E-mail repetido entre irmãos
    UPDATE cadastro.pessoa SET email = (
        SELECT p.email FROM cadastro.pessoa p JOIN pmieducar.aluno al ON al.ref_idpes = p.idpes WHERE al.cod_aluno = a[4])
     WHERE idpes = (SELECT ref_idpes FROM pmieducar.aluno WHERE cod_aluno = a[5]);

    RAISE NOTICE 'Turma %: nome corrigido do aluno %, matrícula % transferida, matrícula % remanejada para a turma %, aluno % com o e-mail do aluno %',
        turma_a, a[1], m[2], m[3], turma_b, a[5], a[4];
END $$;
