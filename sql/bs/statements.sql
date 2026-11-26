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
            $jsonfile,
            -- 'data/json/bs/statements.jsonl',
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

    -- Банк иногда выгружает выписку без остатков (Альфа): берём остаток на начало
    -- из предыдущей выписки счёта, на конец = начало + поступления - списания
    filled AS (
        SELECT
            a.* EXCLUDE (bb, eb),
            coalesce(a.bb, p.prev_eb) AS bb,
            coalesce(a.eb, coalesce(a.bb, p.prev_eb) + coalesce(a.dt, 0) - coalesce(a.cr, 0)) AS eb
        FROM agg a
        LEFT JOIN LATERAL (
            SELECT arg_max(b.eb, b.date_to) AS prev_eb
            FROM agg b
            WHERE b.ba_number = a.ba_number
              AND b.date_to < a.date_from
              AND b.eb IS NOT NULL
        ) p ON TRUE
    ),

    hashed AS (
        SELECT
            *,
            -- md5, а не hash(): hash() DuckDB может меняться между версиями,
            -- а sid хранится в базе и должен быть стабильным
            md5(
                coalesce(ba_number, '')       || '|' ||
                coalesce(date_from::TEXT, '') || '|' ||
                coalesce(date_to::TEXT, '')   || '|' ||
                coalesce(bb::TEXT, '')        || '|' ||
                coalesce(eb::TEXT, '')        || '|' ||
                coalesce(dt::TEXT, '')        || '|' ||
                coalesce(cr::TEXT, '')
            ) AS sid
        FROM filled
    )

    SELECT *
    FROM hashed

    QUALIFY row_number() OVER (
        PARTITION BY sid
        ORDER BY source_file
    ) = 1
    
;


