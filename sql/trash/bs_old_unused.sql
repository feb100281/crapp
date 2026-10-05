-- =============
-- Детали банковских выписок
-- =============
--
-- statement_id операции здесь НЕ прямое поле из JSON (раньше
-- это была ссылка на сырой, ещё не схлопнутый блок
-- СекцияРасчСчет — у Газпромбанка конкретный календарный день).
-- Он пересчитан той же агрегацией и той же формулой id, что и
-- в statements.sql (по файлу+счёту), и присоединён по
-- (source_file, ba_number) — чтобы указывать на итоговую,
-- схлопнутую по файлу выписку, а не на её кусок.

WITH statement_raw AS (
    SELECT
        source_file::TEXT AS source_file,
        NULLIF(TRIM("РасчСчет"), '') AS ba_number,

        try_strptime("ДатаНачала", '%d.%m.%Y')::DATE AS date_from,
        try_strptime("ДатаКонца", '%d.%m.%Y')::DATE AS date_to,

        TRY_CAST("НачальныйОстаток" AS DECIMAL(18,2)) AS bb,
        TRY_CAST("КонечныйОстаток" AS DECIMAL(18,2)) AS eb,
        TRY_CAST("ВсегоПоступило" AS DECIMAL(18,2)) AS dt,
        TRY_CAST("ВсегоСписано" AS DECIMAL(18,2)) AS cr

    FROM read_json_auto(
        $statements_json,
        format = 'newline_delimited',
        union_by_name = true,
        sample_size = -1
    )
),

statement_agg AS (
    SELECT
        source_file,
        ba_number,

        MIN(date_from) AS date_from,
        MAX(date_to)   AS date_to,

        arg_min(bb, date_from) AS bb,
        arg_max(eb, date_to)   AS eb,

        SUM(dt) AS dt,
        SUM(cr) AS cr

    FROM statement_raw
    GROUP BY source_file, ba_number
),

-- Формула ОБЯЗАНА быть идентична той, что в statements.sql —
-- иначе операции не смэтчатся со своими выписками.
statement_ids AS (
    SELECT
        source_file,
        ba_number,

        substr(
            md5(
                coalesce(ba_number, source_file) || '|' ||
                coalesce(date_from::TEXT, '') || '|' ||
                coalesce(date_to::TEXT, '')   || '|' ||
                coalesce(bb::TEXT, '') || '|' ||
                coalesce(eb::TEXT, '') || '|' ||
                coalesce(dt::TEXT, '') || '|' ||
                coalesce(cr::TEXT, '')
            ),
            1, 16
        ) AS statement_id

    FROM statement_agg
),

docs AS (
    SELECT
        "doc_id",
        "ba_number",
        "source_file",
        "СекцияДокумент",

        NULLIF(TRIM("Номер"), '') AS "Номер",
        NULLIF(TRIM("Дата"), '') AS "Дата",
        NULLIF(TRIM("ДатаСписано"), '') AS "ДатаСписано",
        NULLIF(TRIM("ДатаПоступило"), '') AS "ДатаПоступило",
        NULLIF(TRIM("Сумма"), '') AS "Сумма",

        NULLIF(TRIM("ПлательщикИНН"), '') AS "ПлательщикИНН",
        NULLIF(TRIM("Плательщик"), '') AS "Плательщик",
        NULLIF(TRIM("Плательщик1"), '') AS "Плательщик1",
        NULLIF(TRIM("Плательщик2"), '') AS "Плательщик2",

        NULLIF(TRIM("ПлательщикСчет"), '') AS "ПлательщикСчет",
        NULLIF(TRIM("ПлательщикРасчСчет"), '') AS "ПлательщикРасчСчет",

        NULLIF(TRIM("ПолучательИНН"), '') AS "ПолучательИНН",
        NULLIF(TRIM("Получатель"), '') AS "Получатель",
        NULLIF(TRIM("Получатель1"), '') AS "Получатель1",
        NULLIF(TRIM("Получатель2"), '') AS "Получатель2",

        NULLIF(TRIM("ПолучательСчет"), '') AS "ПолучательСчет",
        NULLIF(TRIM("ПолучательРасчСчет"), '') AS "ПолучательРасчСчет",

        NULLIF(TRIM("НазначениеПлатежа"), '') AS "НазначениеПлатежа",
        NULLIF(TRIM("НазначениеПлатежа1"), '') AS "НазначениеПлатежа1",
        NULLIF(TRIM("НазначениеПлатежа2"), '') AS "НазначениеПлатежа2"

    FROM read_json_auto(
        $json_file,
        format = 'newline_delimited',
        union_by_name = true,
        sample_size = -1
    )
)

SELECT
    d.doc_id::TEXT AS doc_id,
    sid.statement_id::TEXT AS statement_id,
    d.ba_number::TEXT AS ba_number,
    d.source_file::TEXT AS source_file,
    d."СекцияДокумент"::TEXT AS doc_type,
    d."Номер"::TEXT AS doc_number,

    try_strptime(d."Дата", '%d.%m.%Y')::DATE AS doc_date,

    COALESCE(
        try_strptime(d."ДатаСписано", '%d.%m.%Y'),
        try_strptime(d."Дата", '%d.%m.%Y')
    )::DATE AS wo_date,

    COALESCE(
        try_strptime(d."ДатаПоступило", '%d.%m.%Y'),
        try_strptime(d."Дата", '%d.%m.%Y')
    )::DATE AS wd_date,

    TRY_CAST(d."Сумма" AS DECIMAL(18,2)) AS amount,

    d."ПлательщикИНН"::TEXT AS inn_payer,

    COALESCE(
        d."Плательщик",
        d."Плательщик1",
        d."Плательщик2"
    ) AS payer_name,

    COALESCE(
        d."ПлательщикРасчСчет",
        d."ПлательщикСчет"
        
    ) AS payer_account,

    d."ПолучательИНН"::TEXT AS inn_receiver,

    COALESCE(
        d."Получатель",
        d."Получатель1",
        d."Получатель2"
    ) AS receiver_name,

    COALESCE(
        d."ПолучательРасчСчет",
        d."ПолучательСчет"
        
    ) AS receiver_account,

    COALESCE(
        d."НазначениеПлатежа",
        d."НазначениеПлатежа1",
        d."НазначениеПлатежа2"
    ) AS description

FROM docs d

LEFT JOIN statement_ids sid
    ON d.source_file = sid.source_file
    AND d.ba_number IS NOT DISTINCT FROM sid.ba_number

-- ============================================================
-- Дедупликация операций.
--
-- В doc_id входит счёт, по которому сделана выписка, поэтому
-- внутренние переводы между своими счетами остаются двумя
-- строками — схлопывается только реально повторно загруженное.
-- ============================================================

QUALIFY row_number() OVER (
    PARTITION BY doc_id
    ORDER BY statement_id, source_file
) = 1

;




