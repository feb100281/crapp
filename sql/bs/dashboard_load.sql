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


CREATE OR REPLACE TABLE target_db.dashboard_cash_check AS
SELECT
    row_number() OVER (ORDER BY abs(k.diff) DESC, k.ba_id) AS id,
    k.ba_id,
    k.number                    AS ba_number,
    COALESCE(g.name, k.number)  AS account_name,
    b.name                      AS bank_name,
    k.opening_rub,
    k.flows_rub,
    k.closing_rub,
    k.diff
FROM cash_flow_check k
LEFT JOIN target_db.treasury_bankaccount a ON a.id = k.ba_id
LEFT JOIN target_db.cp_cp b                ON b.id = a.bank_id
LEFT JOIN target_db.gl_glaccount g         ON g.bank_account_id = k.ba_id;
