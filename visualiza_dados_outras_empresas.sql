-- Permissao: Visualiza dados de outras empresas
-- Use no banco Web/PostgreSQL.
-- A permissao controla consultas de estoque multiempresa dentro do mesmo tenant.

BEGIN;

INSERT INTO public.permissao (acao, descricao)
SELECT
    'estoque:visualizar-outras-empresas',
    'Visualiza dados de outras empresas'
WHERE NOT EXISTS (
    SELECT 1
      FROM public.permissao
     WHERE acao = 'estoque:visualizar-outras-empresas'
);

-- Opcional: liberar para a funcao TW.
-- Remova/comente este bloco se preferir vincular pela tela de funcoes.
INSERT INTO public."_funcaoTopermissao" ("A", "B")
SELECT f.id, p.id
  FROM public.funcao f
 CROSS JOIN public.permissao p
 WHERE UPPER(f.nome) = 'TW'
   AND p.acao = 'estoque:visualizar-outras-empresas'
   AND NOT EXISTS (
       SELECT 1
         FROM public."_funcaoTopermissao" fp
        WHERE fp."A" = f.id
          AND fp."B" = p.id
   );

COMMIT;
