


-- Новый statements
CREATE OR REPLACE TEMP TABLE statements AS
    WITH statements AS (    
        SELECT 
            source_file::TEXT AS source_file,
            statement_id::TEXT AS file_hash,
            NULLIF(TRIM("РасчСчет"), '') AS ba_number,

            try_strptime("ДатаНачала", '%d.%m.%Y')::DATE AS date_from,
            try_strptime("ДатаКонца", '%d.%m.%Y')::DATE AS date_to,

            TRY_CAST("НачальныйОстаток" AS DECIMAL(18,2)) AS bb,
            TRY_CAST("КонечныйОстаток" AS DECIMAL(18,2)) AS eb,
            TRY_CAST("ВсегоПоступило" AS DECIMAL(18,2)) AS dt,
            TRY_CAST("ВсегоСписано" AS DECIMAL(18,2)) AS cr

        FROM read_json_auto(
            'data/json/bs/statements.jsonl',
            format = 'newline_delimited',
            union_by_name = true,
            sample_size = -1
        )
    ),

    agg AS (
        SELECT
            file_hash,
            any_value(source_file) AS source_file,
            ba_number,
            min(date_from) AS date_from,
            max(date_to) AS date_to,
            arg_min(bb, date_from) AS bb,
            arg_max(eb, date_to) AS eb,
            sum(dt) AS dt,
            sum(cr) AS cr

        FROM statements
        GROUP BY file_hash, ba_number
    ),

    hashed AS (
        SELECT
            *,
            hash(
                ba_number,
                date_from,
                date_to,
                bb,
                eb,
                dt,
                cr
            ) AS sid
        FROM agg
    )

    SELECT *
    FROM hashed

    QUALIFY row_number() OVER (
        PARTITION BY sid
        ORDER BY source_file
    ) = 1
    
;
-- Новый bs

CREATE OR REPLACE TEMP TABLE bs_operations AS

    WITH nullsif AS (

        SELECT 
            t.statement_id AS statement_id,
            s.sid::TEXT AS sid,
            s.ba_number::TEXT AS ba_number,

            NULLIF(TRIM(t."СекцияДокумент"), '') AS "СекцияДокумент",

            NULLIF(TRIM(t."Номер"), '') AS "Номер",
            NULLIF(TRIM(t."Дата"), '') AS "Дата",
            NULLIF(TRIM(t."ДатаСписано"), '') AS "ДатаСписано",
            NULLIF(TRIM(t."ДатаПоступило"), '') AS "ДатаПоступило",
            NULLIF(TRIM(t."Сумма"), '') AS "Сумма",

            -- ============================================================
            -- Плательщик
            -- ============================================================

            NULLIF(TRIM(t."ПлательщикИНН"), '') AS "ПлательщикИНН",

            COALESCE(
                NULLIF(TRIM(t."Плательщик"), ''),
                NULLIF(TRIM(t."Плательщик1"), ''),
                NULLIF(TRIM(t."Плательщик2"), '')
            ) AS "Плательщик",

            NULLIF(TRIM(t."ПлательщикСчет"), '') AS "ПлательщикСчет",
            NULLIF(TRIM(t."ПлательщикРасчСчет"), '') AS "ПлательщикРасчСчет",

            NULLIF(TRIM(t."ПлательщикБИК"), '') AS "ПлательщикБИК",

            -- ============================================================
            -- Получатель
            -- ============================================================

            NULLIF(TRIM(t."ПолучательИНН"), '') AS "ПолучательИНН",

            COALESCE(
                NULLIF(TRIM(t."Получатель"), ''),
                NULLIF(TRIM(t."Получатель1"), ''),
                NULLIF(TRIM(t."Получатель2"), '')
            ) AS "Получатель",

            NULLIF(TRIM(t."ПолучательСчет"), '') AS "ПолучательСчет",
            NULLIF(TRIM(t."ПолучательРасчСчет"), '') AS "ПолучательРасчСчет",

            NULLIF(TRIM(t."ПолучательБИК"), '') AS "ПолучательБИК",

            -- ============================================================
            -- Назначение платежа
            -- ============================================================

            NULLIF(TRIM(t."НазначениеПлатежа"), '') AS "НазначениеПлатежа",
            NULLIF(TRIM(t."НазначениеПлатежа1"), '') AS "НазначениеПлатежа1",
            NULLIF(TRIM(t."НазначениеПлатежа2"), '') AS "НазначениеПлатежа2",

            NULLIF(TRIM(t."ПоказательКБК"), '') AS "ПоказательКБК"

        FROM read_json_auto(
            'data/json/bs/operations.jsonl',
            format = 'newline_delimited',
            union_by_name = true,
            sample_size = -1
        ) t

        LEFT JOIN statements s
            ON s.file_hash = t.statement_id
    ),


    -- ============================================================
    -- Приводим исходные данные к финальной структуре
    -- ============================================================

    fin AS (

        SELECT
            sid,
            ba_number,

            "СекцияДокумент"::TEXT AS doc_type,
            "Номер"::TEXT AS doc_number,

            try_strptime(
                "Дата",
                '%d.%m.%Y'
            )::DATE AS doc_date,

            COALESCE(
                try_strptime("ДатаСписано", '%d.%m.%Y'),
                try_strptime("Дата", '%d.%m.%Y')
            )::DATE AS wo_date,

            COALESCE(
                try_strptime("ДатаПоступило", '%d.%m.%Y'),
                try_strptime("Дата", '%d.%m.%Y')
            )::DATE AS wd_date,

            TRY_CAST(
                "Сумма" AS DECIMAL(18,2)
            ) AS amount,


            -- ============================================================
            -- Плательщик
            -- ============================================================

            COALESCE(
                NULLIF(
                    regexp_extract(
                        "Плательщик",
                        '^(?:ИНН\s+)?(\d{10,12})\b',
                        1
                    ),
                    ''
                ),
                "ПлательщикИНН"
            ) AS inn_payer,

            "Плательщик"::TEXT AS payer_name,

            -- Приоритет:
            -- 1. ПлательщикРасчСчет
            -- 2. 20 цифр внутри имени плательщика
            -- 3. ПлательщикСчет
            --
            -- regexp_extract при отсутствии совпадения возвращает '',
            -- поэтому превращаем его в NULL.

            COALESCE(
                "ПлательщикРасчСчет",
                NULLIF(
                    regexp_extract(
                        "Плательщик",
                        '(\d{20})',
                        1
                    ),
                    ''
                ),
                "ПлательщикСчет"
            ) AS payer_account,

            "ПлательщикБИК"::TEXT AS payer_bic,


            -- ============================================================
            -- Получатель
            -- ============================================================

            COALESCE(
                NULLIF(
                    regexp_extract(
                        "Получатель",
                        '^(?:ИНН\s+)?(\d{10,12})\b',
                        1
                    ),
                    ''
                ),
                "ПолучательИНН"
            ) AS inn_receiver,

            "Получатель"::TEXT AS receiver_name,

            -- Приоритет:
            -- 1. ПолучательРасчСчет
            -- 2. 20 цифр внутри имени получателя
            -- 3. ПолучательСчет

            COALESCE(
                "ПолучательРасчСчет",
                NULLIF(
                    regexp_extract(
                        "Получатель",
                        '(\d{20})',
                        1
                    ),
                    ''
                ),
                "ПолучательСчет"
            ) AS receiver_account,

            "ПолучательБИК"::TEXT AS receiver_bic,


            -- ============================================================
            -- Назначение платежа
            -- ============================================================

            COALESCE(
                "НазначениеПлатежа",
                "НазначениеПлатежа1",
                "НазначениеПлатежа2"
            ) AS description,

            "ПоказательКБК"::TEXT AS kbk

        FROM nullsif

        WHERE sid IS NOT NULL
    ),


    -- ============================================================
    -- Делаем уникальный ID операции
    -- ============================================================

    hashed_bs AS (

        SELECT

            hash(
                doc_type,
                doc_number,
                doc_date,
                wo_date,
                wd_date,
                amount,
                ba_number,

                COALESCE(inn_payer, 'inn_payer'),
                COALESCE(payer_name, 'payer'),
                COALESCE(payer_account, 'payer_account'),
                COALESCE(payer_bic, 'payer_bic'),

                COALESCE(inn_receiver, 'inn_receiver'),
                COALESCE(receiver_name, 'receiver'),
                COALESCE(receiver_account, 'receiver_account'),
                COALESCE(receiver_bic, 'receiver_bic'),

                COALESCE(description, 'description')
            )::TEXT AS rr_id,

            *

        FROM fin
    ),


    -- ============================================================
    -- Проверяем внутригрупповые переводы
    --
    -- Если и счёт плательщика, и счёт получателя принадлежат
    -- нашим банковским счетам — операция intercompany.
    -- ============================================================

    intercompany AS (

        SELECT
            *,

            CASE
                WHEN receiver_account IN (
                    SELECT ba_number
                    FROM statements
                )
                AND payer_account IN (
                    SELECT ba_number
                    FROM statements
                )
                THEN 1
                ELSE 0
            END AS intercompany

        FROM hashed_bs
    )


    -- ============================================================
    -- Убираем дубли операций
    -- ============================================================

    SELECT *,
    
    FROM intercompany

    QUALIFY row_number() OVER (
        PARTITION BY rr_id
    ) = 1
;

-- ============================================================
-- GL RAW
-- ============================================================

CREATE OR REPLACE TEMP TABLE bs_gl AS
        with unions as (

        -- ============================================================
        -- ВНЕШНЕЕ ПОСТУПЛЕНИЕ
        -- Наш счёт = получатель
        -- Контрагент = плательщик
        -- ============================================================

        SELECT
            sid,
            rr_id,
            doc_type,
            doc_number,
            doc_date,

            ba_number AS ba_account,
            wd_date AS date_from,

            amount AS dt,
            0::DECIMAL(18,2) AS cr,

            inn_payer AS inn,
            payer_name AS original_name,
            payer_account AS cp_ba,
            payer_bic AS cp_bic,

            kbk,

            intercompany,
            description

        FROM bs_operations
        

        WHERE receiver_account = ba_number
        AND intercompany = FALSE


        UNION ALL


        -- ============================================================
        -- ВНЕШНЕЕ СПИСАНИЕ
        -- Наш счёт = плательщик
        -- Контрагент = получатель
        -- ============================================================

        SELECT
            sid,
            rr_id,
            doc_type,
            doc_number,
            doc_date,

            ba_number AS ba_account,
            wo_date AS date_from,

            0::DECIMAL(18,2) AS dt,
            amount AS cr,

            inn_receiver AS inn,
            receiver_name AS original_name,
            receiver_account AS cp_ba,
            receiver_bic AS cp_bic,

            kbk,

            intercompany,
            description

        FROM bs_operations

        WHERE payer_account = ba_number
        
        AND intercompany = FALSE


        UNION ALL


        -- ============================================================
        -- ВНУТРЕННИЙ ПЕРЕВОД
        -- Одна операция = одна строка
        -- ============================================================

        SELECT
            sid,
            rr_id,
            doc_type,
            doc_number,
            doc_date,

            ba_number AS ba_account,

            doc_date AS date_from,

            CASE
                WHEN receiver_account = ba_number
                THEN amount
                ELSE 0::DECIMAL(18,2)
            END AS dt,

            CASE
                WHEN payer_account = ba_number                    
                THEN amount
                ELSE 0::DECIMAL(18,2)
            END AS cr,

            COALESCE(
                inn_receiver,
                inn_payer
            ) AS inn,

            COALESCE(
                receiver_name,
                payer_name
            ) AS original_name,

            COALESCE(
                receiver_account,
                payer_account
            ) AS cp_ba,

            COALESCE(
                receiver_bic,
                payer_bic
            ) AS cp_bic,

            kbk,

            intercompany,
            description

        FROM bs_operations

        WHERE intercompany = TRUE
        )
        SELECT 
        *,
        NULLIF(
        regexp_extract(
            description,
            '\{(VO\d+)\}',
            1
        ),
        ''
        ) AS vo_code,
        case when len(cp_ba) = 20 then left(cp_ba,5)::text else null end as ba_resolver
        FROM unions
        ;




-- ==================================
-- Resolvers KBK
-- ==================================

select 
kbk,
inn,
sum(dt),
sum(cr),
list(
    DISTINCT
    {
        'inn': inn,
        'original_name': original_name
    }
) as details,
list(description) as descriptions

from bs_gl
where kbk is not null
GROUP BY kbk, inn
;

select 
ba_resolver,
inn,
count(rr_id) as oper_cnt,

sum(dt),
sum(cr),
list(
    DISTINCT
    {
        'inn': inn,
        'original_name': original_name
    }
) as details,
list(DISTINCT description order by description)

        FILTER (WHERE dt != 0) AS descriptions_dt,

list(DISTINCT description)

    FILTER (WHERE cr != 0) AS descriptions_cr


from bs_gl
where ba_resolver is not null
and intercompany = FALSE and kbk is null
GROUP BY ba_resolver, inn
;

select DISTINCT
original_name,
description
from bs_gl
where inn is null
;

select * from bs_gl;



select
inn,
"original_name",
dt,
cr,
"cp_bic",
cp_ba,
description

from bs_gl;

-- важно не удаляем выковыривание комиссии
        SELECT

            rr_id,

            doc_date,

            dt,

            cr,

            description,

            TRY_CAST(

                regexp_extract(

                    description,

                    'Сумма\s+([0-9]+(?:\.[0-9]+)?)\s*руб',

                    1

                )

                AS DECIMAL(18,2)

            ) AS stated_amount,

            TRY_CAST(

                regexp_extract(

                    description,

                    'Комиссия банка\s+([0-9]+(?:\.[0-9]+)?)\s*руб',

                    1

                )

                AS DECIMAL(18,2)

            ) AS stated_fee,

            dt

            -

            TRY_CAST(

                regexp_extract(

                    description,

                    'Сумма\s+([0-9]+(?:\.[0-9]+)?)\s*руб',

                    1

                )

                AS DECIMAL(18,2)

            ) AS dt_vs_amount,

            dt

            -

            (

                TRY_CAST(

                    regexp_extract(

                        description,

                        'Сумма\s+([0-9]+(?:\.[0-9]+)?)\s*руб',

                        1

                    )

                    AS DECIMAL(18,2)

                )

                -

                TRY_CAST(

                    regexp_extract(

                        description,

                        'Комиссия банка\s+([0-9]+(?:\.[0-9]+)?)\s*руб',

                        1

                    )

                    AS DECIMAL(18,2)

                )

            ) AS dt_vs_net

        FROM bs_gl

        WHERE ba_resolver = '30233'

        AND description ILIKE '%Комиссия банка%'

        ORDER BY doc_date;



select * from bs_gl;

select sum(cr) from bs_gl;

select
cp_ba,
list(distinct inn) as inn,
sum(dt) as dt,
sum(cr) as cr,
len(list(distinct inn)) as inn_cnt,
list(
    distinct
    {
        'inn': inn,
        'original_name': original_name
    }
) as details,
list (distinct description) as temp
from bs_gl
where STARTS_WITH(cp_ba,'4') and intercompany = FALSE  
GROUP BY cp_ba;

select
distinct description as temp,
from bs_gl
where STARTS_WITH(cp_ba,'4') and intercompany = FALSE  ;

select * from statements;

select DISTINCT 
substr(ba_number, 6, 3)::text AS currency_number
from statements;

SELECT
    t.sid::TEXT AS sid,
    t.source_file,
    t.ba_number,
    a.id AS ba_id,
    t.date_from,
    t.date_to,
    t.bb,
    t.eb,
    t.dt,
    t.cr
FROM statements t
LEFT JOIN target_db.bank_accounts a
    ON a.number = t.ba_number;       


select * from target_db.bank_accounts;

select
sum(eb)
from statements;


SELECT
cp_ba,
list(distinct inn) as inns,
len(list(distinct inn)) as inn_cnt,
sum(dt) as dt,
sum(cr) as cr,
list( distinct
    {
        'inn': inn,
        'original_name': original_name
    }
) as names,
list(distinct description) as discriptions
from bs_gl
where intercompany = FALSE
GROUP BY cp_ba

;

select 
rr_id,
count(rr_id) as cnt
from bs_operations
group by rr_id
having cnt > 1;
;

select * from bs_operations;

select 
"sid",
sum(dt-cr) as eb
from bs_gl
GROUP BY sid
;

select
sid,
sum(eb)
from statements
GROUP BY sid
;

with a as (
select 
* 

    FROM read_json_auto(
            'data/json/bs/operations.jsonl',
            format = 'newline_delimited',
            union_by_name = true,
            sample_size = -1
        )

)
select
Плательщик,
ПлательщикСчет,
COALESCE(
    NULLIF(TRIM(ПлательщикРасчСчет), ''),
    NULLIF(regexp_extract("Плательщик", '(\d{20})', 1), ''),
    NULLIF(TRIM(ПлательщикСчет), '')
) AS payer_ba,
Сумма
from a
where payer_ba != ПлательщикСчет

;

select DISTINCT
t.ba_number,
f.id
from statements t
LEFT join target_db.macro_fx f 
on f.numeric_code = substr(t.ba_number, 6, 3)::text
;

CREATE OR REPLACE TEMP TABLE bs_gl_adj AS
SELECT
    t.*,
    CASE
        WHEN t.inn IN (SELECT inn FROM target_db.cp_gr)   -- ИНН наш
         AND t.intercompany = 0                           -- не перевод между своими
         AND left(t.cp_ba, 2) <> '40'                     -- счёт контрагента не клиентский
        THEN b.inn                                        -- → ИНН банка нашего счёта
        ELSE t.inn
    END AS inn_adjust
FROM bs_gl t
LEFT JOIN target_db.treasury_bankaccount a
       ON a.number = t.ba_account                         -- наш счёт строки
LEFT JOIN target_db.cp_cp b
       ON b.id = a.bank_id;                               -- банк этого счёта


-- должно вернуть 0 строк
SELECT ba_account, count(*) AS ops
FROM bs_gl_adj
WHERE inn IN (SELECT inn FROM target_db.cp_gr)
  AND intercompany = 0
  AND left(cp_ba, 2) <> '40'
  AND inn_adjust IS NULL
GROUP BY ba_account;

select * from bs_gl_adj;

  COPY ( 
select 
kbk,
sum(dt) as dt,
sum(cr) as cr,
list(DISTINCT description) as description,
list(distinct inn_adjust) as inn_adjust
from bs_gl_adj
where kbk is not null
group by kbk
  )
TO 'data/export/kbk_resolver.csv' (HEADER, DELIMITER ';');


COPY (
select 
ba_resolver,
sum(dt) as dt,
sum(cr) as cr,
list(DISTINCT description) as description,
list(distinct inn_adjust) as inn_adjust
from bs_gl_adj
where ba_resolver is not null
and kbk is null
group by ba_resolver)
TO 'data/export/ba_resolver.csv' (HEADER, DELIMITER ';');

select 
kbk,
sum(dt) as dt,
sum(cr) as cr,
list(DISTINCT description) as description,
list(distinct inn_adjust) as inn_adjust
from bs_gl_adj
where kbk is not null
group by kbk;

select * from bs_gl_adj;