-- ============================================================
-- Переоценка денег: всё в рубли, курсовые разницы по дням
--
-- Нужно: ATTACH базы как target_db. Плейсхолдер __FX_GLOB__
-- (data/parquet/fx/*.parquet) подставляет bs.py.
--
-- Результат — три временные таблицы, bs.py пишет их в parquet:
--   cash_reval     — счёт × день: курс, остатки и обороты в валюте и рублях,
--                    курсовая разница дня. Обороты = строки выписок + проводки
--                    главной книги по банковским счетам (ввод остатков, корректировки)
--   bs_line_rub    — каждая строка выписки в рублях (курс на op_date)
--   reval_check    — где посчитанный остаток ≠ остатку из выписки
--
-- Курс на день = последний опубликованный ЦБ не позже этого дня
-- (ASOF JOIN): на выходные и праздники действует курс пятницы.
--
-- Тождество по каждому дню:
--   bb_rub + dt_rub − cr_rub + fx_diff_rub = eb_rub
-- ============================================================

CREATE OR REPLACE TEMP TABLE fx AS
SELECT code::TEXT AS code, dt::DATE AS dt, rate::DOUBLE AS rate
FROM read_parquet('__FX_GLOB__');


-- проводки главной книги по нашим банковским счетам (ввод остатков + корректировки)
-- оборот в валюте счёта: для валютного — amount_cur, для рублёвого — рубли
CREATE OR REPLACE TEMP TABLE gl_cash AS
SELECT
    l.id                AS journal_line_id,
    e.kind,
    e.date::DATE        AS d,
    e.description,
    l.note,
    l.cf_item_id,
    g.bank_account_id   AS ba_id,
    CASE WHEN l.dt::DOUBLE > 0
         THEN COALESCE(CASE WHEN g.currency_id IS NOT NULL THEN l.amount_cur::DOUBLE END, l.dt::DOUBLE)
         ELSE 0 END     AS dt_cur,
    CASE WHEN l.cr::DOUBLE > 0
         THEN COALESCE(CASE WHEN g.currency_id IS NOT NULL THEN l.amount_cur::DOUBLE END, l.cr::DOUBLE)
         ELSE 0 END     AS cr_cur,
    l.dt::DOUBLE - l.cr::DOUBLE AS amount_rub
FROM target_db.gl_journalline l
JOIN target_db.gl_journalentry e ON e.id = l.entry_id
JOIN target_db.gl_glaccount    g ON g.id = l.account_id
WHERE g.bank_account_id IS NOT NULL;


-- наши счета: валюта, период, входящий остаток.
-- Остаток на начало — из «Ввода остатков» в главной книге (он идёт оборотом
-- на свою дату). Если ввода в журнале нет — из первой выписки (opening_stmt).
CREATE OR REPLACE TEMP TABLE reval_accounts AS
WITH st AS (
    SELECT
        ba_account_id     AS id,
        date_from::DATE   AS df,
        date_to::DATE     AS dt_to,
        bb::DOUBLE        AS bb
    FROM target_db.treasury_statement
    WHERE ba_account_id IS NOT NULL
),
ln AS (
    SELECT ba_account_id AS id, min(op_date::DATE) AS mn, max(op_date::DATE) AS mx
    FROM target_db.treasury_bsline
    GROUP BY 1
),
gl AS (
    SELECT
        ba_id AS id,
        min(d) AS mn,
        max(d) AS mx,
        bool_or(kind = 'OPENING') AS has_opening
    FROM gl_cash
    GROUP BY 1
)
SELECT
    a.id,
    a.number,
    COALESCE(f.code, 'RUB') AS currency,
    least(min(st.df), COALESCE(any_value(ln.mn), min(st.df)), COALESCE(any_value(gl.mn), min(st.df)))       AS min_date,
    greatest(max(st.dt_to), COALESCE(any_value(ln.mx), max(st.dt_to)), COALESCE(any_value(gl.mx), max(st.dt_to))) AS max_date,
    max(st.dt_to)                                                    AS stmt_to,
    COALESCE(any_value(gl.has_opening), FALSE)                       AS has_gl_opening,
    arg_min(st.bb, st.df)                                            AS opening_stmt,
    CASE WHEN COALESCE(any_value(gl.has_opening), FALSE) THEN 0
         ELSE arg_min(st.bb, st.df) END                              AS opening
FROM target_db.treasury_bankaccount a
JOIN st                        ON st.id = a.id
LEFT JOIN ln                   ON ln.id = a.id
LEFT JOIN gl                   ON gl.id = a.id
LEFT JOIN target_db.macro_fx f ON f.id = a.currency_id
GROUP BY a.id, a.number, f.code;


-- Все счета тянутся до самой свежей выписки по компании: остаток счёта,
-- по которому выписки отстают, переносится (валютный — переоценивается),
-- а день помечается stale — выписка по счёту устарела.
CREATE OR REPLACE TEMP TABLE cash_reval AS
WITH days AS (
    SELECT a.id AS ba_id, a.number, a.currency, a.opening, a.stmt_to, g.d::DATE AS d
    FROM reval_accounts a,
         generate_series(
             a.min_date,
             greatest(a.max_date, (SELECT max(max_date) FROM reval_accounts)),
             INTERVAL 1 DAY
         ) AS g(d)
),
rated AS (
    SELECT
        d.*,
        CASE WHEN d.currency = 'RUB' THEN 1.0 ELSE r.rate END AS rate
    FROM days d
    ASOF LEFT JOIN fx r
        ON r.code = d.currency
       AND d.d >= r.dt
),
turnover AS (
    SELECT ba_id, d, sum(dt) AS dt, sum(cr) AS cr
    FROM (
        -- строки выписок
        SELECT
            ba_account_id   AS ba_id,
            op_date::DATE   AS d,
            dt::DOUBLE      AS dt,
            cr::DOUBLE      AS cr
        FROM target_db.treasury_bsline

        UNION ALL

        -- проводки главной книги: ввод остатков и корректировки
        SELECT ba_id, d, dt_cur, cr_cur
        FROM gl_cash
    )
    GROUP BY 1, 2
),
bal AS (
    SELECT
        r.*,
        COALESCE(t.dt, 0) AS base_dt,
        COALESCE(t.cr, 0) AS base_cr,
        r.opening + sum(COALESCE(t.dt, 0) - COALESCE(t.cr, 0))
            OVER (PARTITION BY r.ba_id ORDER BY r.d) AS base_eb
    FROM rated r
    LEFT JOIN turnover t ON t.ba_id = r.ba_id AND t.d = r.d
),
lagged AS (
    SELECT
        *,
        base_eb - base_dt + base_cr                                AS base_bb,
        lag(rate, 1, rate) OVER (PARTITION BY ba_id ORDER BY d)    AS rate_prev
    FROM bal
)
SELECT
    ba_id,
    number,
    currency,
    d                                               AS date,
    rate_prev,
    rate,
    round(base_bb, 2)                               AS base_bb,
    round(base_dt, 2)                               AS base_dt,
    round(base_cr, 2)                               AS base_cr,
    round(base_eb, 2)                               AS base_eb,
    round(base_bb * rate_prev, 2)                   AS bb_rub,
    round(base_dt * rate, 2)                        AS dt_rub,
    round(base_cr * rate, 2)                        AS cr_rub,
    round(base_bb * (rate - rate_prev), 2)          AS fx_diff_rub,
    round(base_eb * rate, 2)                        AS eb_rub,
    stmt_to,                                        -- последняя выписка по счёту
    d > stmt_to AND abs(base_eb) >= 0.01            AS stale  -- остаток есть, выписки нет
FROM lagged
ORDER BY ba_id, d;


CREATE OR REPLACE TEMP TABLE bs_line_rub AS
SELECT
    l.id                              AS line_id,
    l.rr_id,
    l.ba_account_id                   AS ba_id,
    l.op_date::DATE                   AS op_date,
    l.direction,
    l.amount::DOUBLE                  AS amount,
    c.currency,
    c.rate,
    round(l.amount::DOUBLE * c.rate, 2) AS amount_rub
FROM target_db.treasury_bsline l
LEFT JOIN cash_reval c
       ON c.ba_id = l.ba_account_id
      AND c.date  = l.op_date::DATE;


-- остаток по расчёту ≠ остатку из выписки → потеряны строки или выписка
CREATE OR REPLACE TEMP TABLE reval_check AS
SELECT
    s.id            AS statement_id,
    c.number,
    s.date_from::DATE AS date_from,
    s.date_to::DATE   AS date_to,
    s.eb::DOUBLE    AS statement_eb,
    c.base_eb       AS calc_eb,
    round(c.base_eb - s.eb::DOUBLE, 2) AS diff
FROM target_db.treasury_statement s
JOIN cash_reval c
  ON c.ba_id = s.ba_account_id
 AND c.date  = s.date_to::DATE
WHERE abs(c.base_eb - s.eb::DOUBLE) > 0.01;
