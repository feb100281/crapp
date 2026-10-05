-- Проверки после bs_gl_adj.sql — запускать руками в той же сессии DuckDB

-- ============================================================
-- Проверки
-- ============================================================

-- 1. «Космо» на счетах банка без банка счёта — должно быть 0 строк
SELECT ba_account, count(*) AS ops
FROM bs_gl_adj
WHERE inn IN (SELECT inn FROM target_db.cp_gr)
  AND intercompany = 0
  AND left(cp_ba, 2) <> '40'
  AND inn_adjust IS NULL
GROUP BY ba_account;

-- 2. Комиссия больше суммы — смотреть глазами
SELECT doc_date, amount, fee_cr, description
FROM bs_gl_adj
WHERE fee_cr >= amount;

-- 2a. Сходимость: gross_dt − удержанное = dt (должно быть 0 строк)
SELECT rr_id, dt, gross_dt, fee_cr, debt_cr
FROM bs_gl_adj
WHERE direction = 1
  AND gross_dt
      - CASE WHEN fee_withheld THEN fee_cr ELSE 0 END
      - COALESCE(debt_cr, 0) <> dt;

-- 3. Косяки НДС: сумма НДС не сходится со ставкой
SELECT doc_date, amount, vat_rate, vat_amount,
       round(amount * vat_rate / (100 + vat_rate), 2) AS vat_expected,
       description
FROM bs_gl_adj
WHERE vat_check = FALSE
ORDER BY amount DESC;


