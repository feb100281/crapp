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


-- Касса — такой же счёт, как банковский (BankAccount с типом CASH, номер = счёт 1С «50.01»).
-- Движение кассы:
--   CASH          — строки карточки 50 из 1С, КРОМЕ сдачи в банк (корр. 57.*) и строк,
--                   которых уже нет в 1С (in_1c = 0)
--   CASH_TO_BANK  — сдача в банк берётся не из 1С, а из банковских выписок (взносы наличных
--                   на наши счета), со знаком «расход кассы». Так касса сходится с банком,
--                   а «деньги в пути» видно как разницу с сальдо 1С.
CREATE OR REPLACE TEMP TABLE cash_desk_moves AS
WITH acc AS (
    SELECT id, number FROM target_db.treasury_bankaccount WHERE ba_type = 'CASH'
)
SELECT
    'CASH'                      AS src,
    l.id                        AS ref_id,
    a.id                        AS ba_id,
    l.date::DATE                AS d,
    CASE WHEN l.direction = 1 THEN l.amount::DOUBLE ELSE 0 END AS dt,
    CASE WHEN l.direction = 2 THEN l.amount::DOUBLE ELSE 0 END AS cr,
    -- «назначение» кассы: документ · статья 1С · основание · договор · документ расчётов
    -- (договор «Основной договор» и ссылка документа на самого себя не повторяются)
    concat_ws(' · ', NULLIF(trim(l.doc_type || ' ' || l.doc_number), ''),
              NULLIF(l.cf_item_1c, '<...>'), NULLIF(l.doc_comment, ''),
              CASE WHEN trim(COALESCE(l.subconto2, '')) NOT IN ('', '<...>', 'Основной договор')
                   THEN l.subconto2 END,
              CASE WHEN trim(COALESCE(l.subconto3, '')) NOT IN ('', '<...>')
                    AND l.subconto3 NOT LIKE l.doc_type || ' ' || l.doc_number || '%'
                   THEN l.subconto3 END)                                     AS description,
    NULLIF(NULLIF(l.counterparty, ''), '<...>')                            AS cp_name
FROM target_db.treasury_cashline l
JOIN acc a ON a.number = l.cash_account
WHERE CAST(l.in_1c AS INTEGER) = 1
  AND l.corr_account NOT LIKE '57%'

UNION ALL

-- взносы наличных по выпискам: Альфа 20208 / «Внес.ср» (через 30233), ГПБ 30232 «внесенной … выручки»
SELECT
    'CASH_TO_BANK',
    b.id,
    (SELECT min(id) FROM acc),
    b.op_date::DATE,
    0,
    b.amount::DOUBLE,
    'Сдано в банк на ' || ba.number || ' · ' || b.description,
    'ООО «Космо»'
FROM target_db.treasury_bsline b
JOIN target_db.treasury_bankaccount ba ON ba.id = b.ba_account_id
WHERE b.direction = 1
  AND EXISTS (SELECT 1 FROM acc)
  AND (
        b.cp_ba LIKE '20208%'
     OR b.description LIKE '%Внес.ср%'
     OR (b.cp_ba LIKE '30232%' AND regexp_matches(b.description, '(?i)внесенн.*выручк'))
  );


-- «выписка» кассы: период выгрузки 1С, сальдо 1С на начало (до первой строки)
-- и на конец (после последней) — для сверки и признака «касса не выгружена»
CREATE OR REPLACE TEMP TABLE cash_desk_accounts AS
WITH l AS (
    SELECT
        a.id AS ba_id,
        c.date::DATE AS d,
        c.balance_1c::DOUBLE AS balance_1c,
        CASE WHEN c.direction = 1 THEN 1 ELSE -1 END * c.amount::DOUBLE AS signed,
        c.source_file,
        row_number() OVER (PARTITION BY a.id ORDER BY c.date, c.doc_datetime NULLS FIRST, c.id)      AS rn_first,
        row_number() OVER (PARTITION BY a.id ORDER BY c.date DESC, c.doc_datetime DESC NULLS LAST, c.id DESC) AS rn_last
    FROM target_db.treasury_cashline c
    JOIN target_db.treasury_bankaccount a ON a.ba_type = 'CASH' AND a.number = c.cash_account
    WHERE CAST(c.in_1c AS INTEGER) = 1
)
SELECT
    ba_id,
    min(d)                                              AS df,
    max(d)                                              AS dt_to,
    max(CASE WHEN rn_first = 1 THEN balance_1c - signed END) AS bb,
    max(CASE WHEN rn_last = 1 THEN balance_1c END)      AS eb,
    count(DISTINCT source_file)                         AS files
FROM l
GROUP BY ba_id;


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

    UNION ALL

    -- касса: «выписка» = период выгрузки 1С
    SELECT ba_id, df, dt_to, bb FROM cash_desk_accounts
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
    any_value(a.closed_on)::DATE                                     AS closed_on,
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
-- Закрытый счёт (closed_on) обрывается на дате закрытия.
CREATE OR REPLACE TEMP TABLE cash_reval AS
WITH days AS (
    SELECT a.id AS ba_id, a.number, a.currency, a.opening, a.stmt_to, g.d::DATE AS d
    FROM reval_accounts a,
         generate_series(
             a.min_date,
             least(greatest(a.max_date, (SELECT max(max_date) FROM reval_accounts)),
                   COALESCE(a.closed_on, DATE '9999-12-31')),
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

        UNION ALL

        -- касса: строки 1С без сдачи в банк + взносы по выпискам
        SELECT ba_id, d, dt, cr
        FROM cash_desk_moves
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
