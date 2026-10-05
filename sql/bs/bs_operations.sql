-- ============================================================
-- bs_operations — операции из JSONL, нормализованные
--
-- Нужна временная таблица statements (sql/bs/statements.sql).
-- Параметр: $operations_json — путь к operations.jsonl
--
-- rr_id = md5 реквизитов операции. В rr_id входит наш счёт
-- (ba_number), поэтому перевод между своими счетами остаётся
-- двумя строками — по одной в выписке каждого счёта.
-- Операции из файла-дубля (выписка уже загружена под другим
-- именем) отсекаются: у них нет sid в statements.
-- ============================================================

CREATE OR REPLACE TEMP TABLE bs_operations AS

WITH nullsif AS (

    SELECT
        s.sid::TEXT       AS sid,
        s.ba_number::TEXT AS ba_number,

        NULLIF(TRIM(t."СекцияДокумент"), '') AS "СекцияДокумент",
        NULLIF(TRIM(t."Номер"), '')          AS "Номер",
        NULLIF(TRIM(t."Дата"), '')           AS "Дата",
        NULLIF(TRIM(t."ДатаСписано"), '')    AS "ДатаСписано",
        NULLIF(TRIM(t."ДатаПоступило"), '')  AS "ДатаПоступило",
        NULLIF(TRIM(t."Сумма"), '')          AS "Сумма",

        NULLIF(TRIM(t."ПлательщикИНН"), '') AS "ПлательщикИНН",
        COALESCE(
            NULLIF(TRIM(t."Плательщик"), ''),
            NULLIF(TRIM(t."Плательщик1"), ''),
            NULLIF(TRIM(t."Плательщик2"), '')
        ) AS "Плательщик",
        NULLIF(TRIM(t."ПлательщикСчет"), '')     AS "ПлательщикСчет",
        NULLIF(TRIM(t."ПлательщикРасчСчет"), '') AS "ПлательщикРасчСчет",
        NULLIF(TRIM(t."ПлательщикБИК"), '')      AS "ПлательщикБИК",

        NULLIF(TRIM(t."ПолучательИНН"), '') AS "ПолучательИНН",
        COALESCE(
            NULLIF(TRIM(t."Получатель"), ''),
            NULLIF(TRIM(t."Получатель1"), ''),
            NULLIF(TRIM(t."Получатель2"), '')
        ) AS "Получатель",
        NULLIF(TRIM(t."ПолучательСчет"), '')     AS "ПолучательСчет",
        NULLIF(TRIM(t."ПолучательРасчСчет"), '') AS "ПолучательРасчСчет",
        NULLIF(TRIM(t."ПолучательБИК"), '')      AS "ПолучательБИК",

        NULLIF(TRIM(t."НазначениеПлатежа"), '')  AS "НазначениеПлатежа",
        NULLIF(TRIM(t."НазначениеПлатежа1"), '') AS "НазначениеПлатежа1",
        NULLIF(TRIM(t."НазначениеПлатежа2"), '') AS "НазначениеПлатежа2",

        -- «0» и пусто — это не КБК
        NULLIF(NULLIF(TRIM(t."ПоказательКБК"), ''), '0') AS "ПоказательКБК"

    FROM read_json_auto(
        $operations_json,
        format = 'newline_delimited',
        union_by_name = true,
        sample_size = -1
    ) t

    LEFT JOIN statements s
        ON s.file_hash = t.statement_id
),

fin AS (

    SELECT
        sid,
        ba_number,

        "СекцияДокумент"::TEXT AS doc_type,
        "Номер"::TEXT          AS doc_number,

        try_strptime("Дата", '%d.%m.%Y')::DATE AS doc_date,

        COALESCE(
            try_strptime("ДатаСписано", '%d.%m.%Y'),
            try_strptime("Дата", '%d.%m.%Y')
        )::DATE AS wo_date,

        COALESCE(
            try_strptime("ДатаПоступило", '%d.%m.%Y'),
            try_strptime("Дата", '%d.%m.%Y')
        )::DATE AS wd_date,

        TRY_CAST("Сумма" AS DECIMAL(18, 2)) AS amount,

        -- Плательщик: ИНН из начала имени → поле ИНН
        COALESCE(
            NULLIF(regexp_extract("Плательщик", '^(?:ИНН\s+)?(\d{10,12})\b', 1), ''),
            "ПлательщикИНН"
        ) AS inn_payer,
        "Плательщик"::TEXT AS payer_name,
        COALESCE(
            "ПлательщикРасчСчет",
            NULLIF(regexp_extract("Плательщик", '(\d{20})', 1), ''),
            "ПлательщикСчет"
        ) AS payer_account,
        "ПлательщикБИК"::TEXT AS payer_bic,

        -- Получатель
        COALESCE(
            NULLIF(regexp_extract("Получатель", '^(?:ИНН\s+)?(\d{10,12})\b', 1), ''),
            "ПолучательИНН"
        ) AS inn_receiver,
        "Получатель"::TEXT AS receiver_name,
        COALESCE(
            "ПолучательРасчСчет",
            NULLIF(regexp_extract("Получатель", '(\d{20})', 1), ''),
            "ПолучательСчет"
        ) AS receiver_account,
        "ПолучательБИК"::TEXT AS receiver_bic,

        COALESCE(
            "НазначениеПлатежа",
            "НазначениеПлатежа1",
            "НазначениеПлатежа2"
        ) AS description,

        "ПоказательКБК"::TEXT AS kbk

    FROM nullsif
    WHERE sid IS NOT NULL
),

keyed AS (

    SELECT
        coalesce(doc_type, '')            || '|' ||
        coalesce(doc_number, '')          || '|' ||
        coalesce(doc_date::TEXT, '')      || '|' ||
        coalesce(wo_date::TEXT, '')       || '|' ||
        coalesce(wd_date::TEXT, '')       || '|' ||
        coalesce(amount::TEXT, '')        || '|' ||
        coalesce(ba_number, '')           || '|' ||
        coalesce(inn_payer, '')           || '|' ||
        coalesce(payer_name, '')          || '|' ||
        coalesce(payer_account, '')       || '|' ||
        coalesce(payer_bic, '')           || '|' ||
        coalesce(inn_receiver, '')        || '|' ||
        coalesce(receiver_name, '')       || '|' ||
        coalesce(receiver_account, '')    || '|' ||
        coalesce(receiver_bic, '')        || '|' ||
        coalesce(description, '')         AS rr_key,
        *
    FROM fin
),

-- Одинаковые строки (в т.ч. внутри одной выписки) схлопываются в одну.
-- Проверено на ОТП 27.04.2024: банк выгрузил комиссию 2 500 ₽ дважды, а в итогах
-- выписки (ВсегоСписано) её нет вообще — такие случаи правятся ручной проводкой.
hashed AS (
    SELECT md5(rr_key) AS rr_id, * EXCLUDE (rr_key)
    FROM keyed
)

SELECT
    *,
    -- оба счёта — наши → перевод между своими
    CASE
        WHEN receiver_account IN (SELECT ba_number FROM statements)
         AND payer_account    IN (SELECT ba_number FROM statements)
        THEN 1 ELSE 0
    END AS intercompany

FROM hashed

QUALIFY row_number() OVER (PARTITION BY rr_id ORDER BY sid) = 1
;
