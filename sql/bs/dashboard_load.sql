-- ============================================================
-- Витрины дашборда: временные таблицы расчёта → SQLite (target_db)
--
-- Таблицы целиком заменяются при каждом пересчёте. Жирные: всё нужное для
-- отчётов лежит в строке, связей со справочниками нет. Django читает их
-- моделями dashboard (managed = False), сам их не создаёт и не меняет.
--
-- Даты — текстом ISO, логические — 0/1: так их без сюрпризов читает Django.
-- ============================================================

CREATE OR REPLACE TABLE target_db.dashboard_cash_flow AS
SELECT
    row_number() OVER (ORDER BY date, ba_id, line_id, journal_line_id, source) AS id,
    source,
    strftime(date, '%Y-%m-%d')        AS date,
    year,
    month,
    period,
    ba_id,
    ba_number,
    account_name,
    bank_name,
    currency,
    line_id,
    journal_line_id,
    amount_cur,
    rate,
    amount_rub,
    activity,
    activity_name,
    direction,
    direction_name,
    article_code,
    article_name,
    cf_code,
    cf_name,
    is_conversion::INTEGER            AS is_conversion,
    cp_name,
    inn,
    description
FROM cash_flow;


CREATE OR REPLACE TABLE target_db.dashboard_cash_balance AS
SELECT
    row_number() OVER (ORDER BY c.ba_id, c.date)  AS id,
    strftime(c.date, '%Y-%m-%d')                  AS date,
    year(c.date)                                  AS year,
    month(c.date)                                 AS month,
    strftime(c.date, '%Y-%m')                     AS period,
    c.ba_id,
    c.number                                      AS ba_number,
    COALESCE(g.name, c.number)                    AS account_name,
    b.name                                        AS bank_name,
    c.currency,
    c.rate_prev,
    c.rate,
    c.base_bb,
    c.base_dt,
    c.base_cr,
    c.base_eb,
    c.bb_rub,
    c.dt_rub,
    c.cr_rub,
    c.fx_diff_rub,
    c.eb_rub,
    strftime(c.stmt_to, '%Y-%m-%d')               AS stmt_to,
    c.stale::INTEGER                              AS stale
FROM cash_reval c
LEFT JOIN target_db.treasury_bankaccount a ON a.id = c.ba_id
LEFT JOIN target_db.cp_cp b                ON b.id = a.bank_id
LEFT JOIN target_db.gl_glaccount g         ON g.bank_account_id = c.ba_id;


-- Остатки по дням, свёрнутые по всем счетам (в рублях). Счета дня —
-- в dashboard_cash_balance с той же датой (связь по date, без FK).
CREATE OR REPLACE TABLE target_db.dashboard_cash_balance_day AS
SELECT
    row_number() OVER (ORDER BY date)  AS id,
    strftime(date, '%Y-%m-%d')         AS date,
    year(date)                         AS year,
    month(date)                        AS month,
    strftime(date, '%Y-%m')            AS period,
    count(*)                           AS accounts,
    sum(bb_rub)                        AS bb_rub,
    sum(dt_rub)                        AS dt_rub,
    sum(cr_rub)                        AS cr_rub,
    sum(fx_diff_rub)                   AS fx_diff_rub,
    sum(eb_rub)                        AS eb_rub,
    count(*) FILTER (WHERE stale)      AS stale_accounts,
    COALESCE(sum(eb_rub) FILTER (WHERE stale), 0) AS stale_rub
FROM cash_reval
GROUP BY date;


-- Сверка с банком по каждому счёту:
--   • конечный остаток последней выписки против нашего расчёта на ту же дату
--     (расчёт = ввод остатков + все строки выписок + проводки ГК);
--   • разрывы в выписках (gaps) — то, что объясняет расхождение:
--       – стык: следующая выписка начинается позже, чем закончилась
--         предыдущая (в т.ч. на следующий день), а её входящий остаток ≠
--         исходящему предыдущей. Обычно выписку выгрузили посреди дня, и
--         операции после выгрузки не попали ни в один файл;
--       – строки ≠ итогам: сумма строк выписки не равна «Всего поступило /
--         списано» из её же шапки (дубли, задвоенные документы в файле банка).
--         Только для выписок, которые не пересекаются с другими по этому счёту.
--       – закрытый счёт с ненулевым остатком на дату закрытия.
--     gap_amount — сколько расхождения (расчёт − банк) объясняют эти разрывы.
--   • внутренняя арифметика отчёта (начало + ДДС = конец) — колонка check_rub.
CREATE OR REPLACE TABLE target_db.dashboard_cash_check AS
WITH st AS (
    SELECT
        id                  AS sid,
        ba_account_id       AS ba_id,
        source_file,
        date_from::DATE     AS df,
        date_to::DATE       AS dt,
        bb::DOUBLE          AS bb,
        eb::DOUBLE          AS eb,
        t.dt::DOUBLE        AS s_in,
        t.cr::DOUBLE        AS s_out
    FROM target_db.treasury_statement t
    WHERE ba_account_id IS NOT NULL
),
last_st AS (
    SELECT ba_id, max(dt) AS stmt_to, arg_max(eb, dt) AS bank_eb, count(*) AS statements
    FROM st
    GROUP BY ba_id
),
seq AS (
    SELECT
        *,
        lag(dt) OVER (PARTITION BY ba_id ORDER BY df, dt) AS prev_to,
        lag(eb) OVER (PARTITION BY ba_id ORDER BY df, dt) AS prev_eb
    FROM st
),
breaks AS (
    -- стык выписок, где остаток не продолжается
    SELECT
        ba_id,
        df AS on_day,
        CASE WHEN df = prev_to + 1
             THEN 'стык ' || strftime(prev_to, '%d.%m') || '→' || strftime(df, '%d.%m.%Y')
                  || ': остаток ' || printf('%.2f', prev_eb) || ' → ' || printf('%.2f', bb)
             ELSE 'нет выписок ' || strftime(prev_to + 1, '%d.%m.%Y') || '–' || strftime(df - 1, '%d.%m.%Y')
        END AS note,
        prev_eb - bb AS impact
    FROM seq
    WHERE prev_to IS NOT NULL
      AND df > prev_to
      AND abs(bb - prev_eb) >= 0.01
),
st_lines AS (
    SELECT statement_id AS sid, sum(dt) AS l_in, sum(cr) AS l_out
    FROM target_db.treasury_bsline
    GROUP BY statement_id
),
mismatch AS (
    -- строки выписки не бьются с её итогами
    SELECT
        s.ba_id,
        s.df AS on_day,
        'строки ≠ итогам: ' || s.source_file || ' ('
            || printf('%+.2f', (COALESCE(l.l_in, 0) - COALESCE(l.l_out, 0)) - (s.s_in - s.s_out)) || ')' AS note,
        (COALESCE(l.l_in, 0) - COALESCE(l.l_out, 0)) - (s.s_in - s.s_out) AS impact
    FROM st s
    LEFT JOIN st_lines l ON l.sid = s.sid
    WHERE abs((COALESCE(l.l_in, 0) - COALESCE(l.l_out, 0)) - (s.s_in - s.s_out)) >= 0.01
      AND NOT EXISTS (
          SELECT 1 FROM st o
          WHERE o.ba_id = s.ba_id AND o.sid <> s.sid
            AND o.df <= s.dt AND o.dt >= s.df
      )
),
closed AS (
    -- счёт закрыт, а остаток на дату закрытия не нулевой
    SELECT
        a.id AS ba_id,
        a.closed_on::DATE AS on_day,
        'закрыт ' || strftime(a.closed_on::DATE, '%d.%m.%Y') || ' с остатком ' || printf('%.2f', c.base_eb) AS note,
        0.0 AS impact
    FROM target_db.treasury_bankaccount a
    JOIN cash_reval c ON c.ba_id = a.id AND c.date = a.closed_on::DATE
    WHERE a.closed_on IS NOT NULL
      AND abs(c.base_eb) >= 0.01
),
gaps AS (
    SELECT
        ba_id,
        count(*) AS gap_count,
        string_agg(note, '; ' ORDER BY on_day) AS gaps,
        sum(impact) AS gap_amount
    FROM (SELECT * FROM breaks UNION ALL SELECT * FROM mismatch UNION ALL SELECT * FROM closed)
    GROUP BY ba_id
)
SELECT
    row_number() OVER (ORDER BY abs(COALESCE(c.base_eb - l.bank_eb, 0) * COALESCE(c.rate, 1)) DESC, k.ba_id) AS id,
    k.ba_id,
    k.number                                    AS ba_number,
    COALESCE(g.name, k.number)                  AS account_name,
    b.name                                      AS bank_name,
    c.currency,
    strftime(l.stmt_to, '%Y-%m-%d')             AS stmt_to,
    l.statements,
    round(l.bank_eb, 2)                         AS bank_eb,
    round(c.base_eb, 2)                         AS calc_eb,
    round(c.base_eb - l.bank_eb, 2)             AS diff_cur,
    round((c.base_eb - l.bank_eb) * c.rate, 2)  AS diff_rub,
    COALESCE(gp.gap_count, 0)                   AS gap_count,
    gp.gaps,
    round(gp.gap_amount, 2)                     AS gap_amount,
    k.opening_rub,
    k.flows_rub,
    k.closing_rub,
    k.diff                                      AS check_rub
FROM cash_flow_check k
LEFT JOIN last_st l    ON l.ba_id = k.ba_id
LEFT JOIN cash_reval c ON c.ba_id = k.ba_id AND c.date = l.stmt_to
LEFT JOIN gaps gp      ON gp.ba_id = k.ba_id
LEFT JOIN target_db.treasury_bankaccount a ON a.id = k.ba_id
LEFT JOIN target_db.cp_cp b                ON b.id = a.bank_id
LEFT JOIN target_db.gl_glaccount g         ON g.bank_account_id = k.ba_id;
