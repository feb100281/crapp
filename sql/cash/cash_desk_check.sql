-- ============================================================
-- Проверка кассы: наш остаток (cash_desk_flow) против сальдо 1С по дням.
--
-- Нужно: сначала cash_desk_flow.sql (временные таблицы cash_desk_flow, bank_cash_in).
--
-- Результат — временные таблицы:
--   cash_desk_daily  — день: наш остаток, сальдо 1С, «в пути» = наш − 1С
--   cash_desk_month  — месяц: сдано в банк по 1С (57.01) и по выпискам, разница,
--                      «в пути» на конец месяца
--
-- Как читать «в пути»:
--   + 1С уже списала из кассы на 57.01, банк ещё не зачислил (нормально 1–3 дня)
--   − банк зачислил взнос, а в 1С его нет / он позже → наличные мимо кассы 1С
--   висит неделями или растёт → косяк учёта кассы или нет банковской выписки
-- ============================================================

CREATE OR REPLACE TEMP TABLE cash_desk_daily AS
WITH cash AS (
    SELECT * FROM read_parquet('__CASH_DIR__/cash_desk.parquet')
),
moves AS (                         -- движение по нашему расчёту
    SELECT date AS d, sum(amount_rub) AS amount
    FROM cash_desk_flow
    GROUP BY 1
),
onec AS (                          -- сальдо 1С на конец дня (последняя строка дня)
    SELECT date AS d, arg_max(balance::DOUBLE, doc_datetime) AS balance_1c
    FROM cash
    GROUP BY 1
),
days AS (
    SELECT d FROM moves UNION SELECT d FROM onec
)
SELECT
    d.d                                                         AS date,
    round(sum(COALESCE(m.amount, 0)) OVER (ORDER BY d.d), 2)    AS balance_ours,
    -- в дни без строк в 1С сальдо 1С не меняется
    round(last_value(o.balance_1c IGNORE NULLS) OVER (ORDER BY d.d), 2) AS balance_1c
FROM days d
LEFT JOIN moves m ON m.d = d.d
LEFT JOIN onec  o ON o.d = d.d;

ALTER TABLE cash_desk_daily ADD COLUMN in_transit DOUBLE;
UPDATE cash_desk_daily SET in_transit = round(balance_ours - balance_1c, 2);


CREATE OR REPLACE TEMP TABLE cash_desk_month AS
WITH cash AS (
    SELECT * FROM read_parquet('__CASH_DIR__/cash_desk.parquet')
),
to_bank_1c AS (
    SELECT strftime(date, '%Y-%m') AS period, count(*) AS n_1c, sum(amount::DOUBLE) AS sum_1c
    FROM cash
    WHERE corr_account = '57.01'
    GROUP BY 1
),
to_bank_stmt AS (
    SELECT strftime(d, '%Y-%m') AS period, count(*) AS n_bank, sum(amount) AS sum_bank
    FROM bank_cash_in
    GROUP BY 1
),
month_end AS (
    SELECT strftime(date, '%Y-%m') AS period,
           arg_max(balance_ours, date) AS balance_ours,
           arg_max(balance_1c, date)   AS balance_1c,
           arg_max(in_transit, date)   AS in_transit
    FROM cash_desk_daily
    GROUP BY 1
)
SELECT
    p.period,
    COALESCE(a.n_1c, 0)                                   AS n_1c,
    round(COALESCE(a.sum_1c, 0), 2)                       AS to_bank_1c,
    COALESCE(b.n_bank, 0)                                 AS n_bank,
    round(COALESCE(b.sum_bank, 0), 2)                     AS to_bank_stmt,
    round(COALESCE(a.sum_1c, 0) - COALESCE(b.sum_bank, 0), 2) AS diff,
    e.balance_ours,
    e.balance_1c,
    e.in_transit
FROM (SELECT period FROM month_end
      UNION SELECT period FROM to_bank_1c
      UNION SELECT period FROM to_bank_stmt) p
LEFT JOIN to_bank_1c   a ON a.period = p.period
LEFT JOIN to_bank_stmt b ON b.period = p.period
LEFT JOIN month_end    e ON e.period = p.period
ORDER BY p.period;
