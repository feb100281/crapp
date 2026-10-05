-- ============================================================
-- ДДС в рублях — одна таблица cash_flow, bs.py пишет её в parquet
--
-- Нужны: target_db + временные таблицы cash_reval.sql (курсы по дням).
--
-- Знак amount_rub: + поступление, − выплата.
-- Σ amount_rub по счёту (включая OPENING) = остаток на конец в рублях.
--
-- Колонки для группировки: activity / activity_name (0 остаток на начало,
-- 1 операционная, 2 инвестиционная, 3 финансовая, 4 внутригрупповые,
-- 5 курсовые разницы, 9 не разнесено), direction / direction_name
-- (поступления / выплаты), year, month, period ('2024-05').
--
-- source:
--   BANK     — разноска строки выписки (BSLineAlloc)
--   UNALLOC  — строка выписки (или её неразнесённый остаток) без статьи
--   FEE      — комиссия, удержанная банком из поступления → 121103
--   DEBT     — «с уч. гаш. долга», удержанное банком из эквайринга → 121104 (банковские комиссии)
--
-- Удержания раскладываются ЗДЕСЬ, автоматически: поступление показывается
-- gross (сумма строки + удержанное), а удержанное — отдельной выплатой.
-- В сумме по строке: gross − комиссия − долг = сумма строки.
--   OPENING  — остаток на начало: «Ввод остатков» из главной книги, а если
--              его там нет — входящий остаток первой выписки
--   MANUAL   — ручная проводка по банковскому счёту (корректировка); рубли — из проводки
--   FX       — курсовая разница на остаток валютного счёта (по дням)
--
-- Результат конвертации (банк продаёт валюту не по курсу ЦБ) отдельной
-- строкой не выделяется: это Σ amount_rub по статьям 410200 + 420200
-- (is_conversion = TRUE). Ноль там будет, только если курс банка = курсу ЦБ.
-- ============================================================

CREATE OR REPLACE TEMP TABLE cf_items AS
SELECT
    i.id,
    i.code,
    i.name,
    COALESCE(p.code, i.code) AS article_code,
    COALESCE(p.name, i.name) AS article_name,
    i.activity,
    i.direction
FROM target_db.treasury_cfitem i
LEFT JOIN target_db.treasury_cfitem p ON p.id = i.parent_id;


CREATE OR REPLACE TEMP TABLE cash_flow_raw AS
WITH ln_src AS (
    SELECT
        l.id,
        l.ba_account_id                 AS ba_id,
        l.op_date::DATE                 AS d,
        l.direction,
        l.amount::DOUBLE                AS amount,
        CASE WHEN l.direction = 1 THEN 1 ELSE -1 END * l.amount::DOUBLE AS signed,
        -- удержано банком из поступления: комиссия (эквайринг карт) и долг (ГПБ)
        CASE WHEN l.direction = 1 AND l.fee_withheld::BOOLEAN
             THEN COALESCE(l.fee_cr::DOUBLE, 0) ELSE 0 END  AS fee,
        CASE WHEN l.direction = 1
             THEN COALESCE(l.debt_cr::DOUBLE, 0) ELSE 0 END AS debt,
        l.description,
        l.cp_name,
        l.inn_adjust
    FROM target_db.treasury_bsline l
),
alloc AS (
    SELECT
        a.line_id,
        a.cf_item_id,
        CASE WHEN a.direction = 1 THEN 1 ELSE -1 END * a.amount::DOUBLE AS signed,
        -- удержания добавляются к самой крупной разноске строки
        row_number() OVER (
            PARTITION BY a.line_id
            ORDER BY CASE WHEN a.direction = 1 THEN 1 ELSE -1 END * a.amount::DOUBLE DESC, a.id
        ) AS rn
    FROM target_db.treasury_bslinealloc a
),
-- неразнесённое: строки без разноски и «хвосты», если разноска не на всю сумму
unalloc AS (
    SELECT
        ln.id AS line_id,
        ln.signed - COALESCE(sum(al.signed), 0) AS signed,
        count(al.line_id) = 0                   AS no_alloc
    FROM ln_src ln
    LEFT JOIN alloc al ON al.line_id = ln.id
    GROUP BY ln.id, ln.signed
    HAVING abs(ln.signed - COALESCE(sum(al.signed), 0)) > 0.005
),
flows_raw AS (
    -- разноска; к первой (крупнейшей) добавляем удержанное → выручка gross
    SELECT 'BANK' AS source, al.line_id, NULL::BIGINT AS journal_line_id,
           ln.ba_id, ln.d, al.cf_item_id,
           al.signed + CASE WHEN al.rn = 1 THEN ln.fee + ln.debt ELSE 0 END AS signed_cur,
           ln.description, ln.cp_name, ln.inn_adjust
    FROM alloc al
    JOIN ln_src ln ON ln.id = al.line_id

    UNION ALL

    -- без разноски — тоже gross
    SELECT 'UNALLOC', u.line_id, NULL,
           ln.ba_id, ln.d, NULL,
           u.signed + CASE WHEN u.no_alloc THEN ln.fee + ln.debt ELSE 0 END,
           ln.description, ln.cp_name, ln.inn_adjust
    FROM unalloc u
    JOIN ln_src ln ON ln.id = u.line_id

    UNION ALL

    -- удержанная комиссия → 121103 «Эквайринг и СБП»
    SELECT 'FEE', ln.id, NULL,
           ln.ba_id, ln.d, (SELECT id FROM cf_items WHERE code = '121103'),
           -ln.fee,
           ln.description, ln.cp_name, ln.inn_adjust
    FROM ln_src ln
    WHERE ln.fee > 0

    UNION ALL

    -- удержанное из эквайринга «в счёт долга» → 121104 «Удержания банка из эквайринга»
    SELECT 'DEBT', ln.id, NULL,
           ln.ba_id, ln.d, (SELECT id FROM cf_items WHERE code = '121104'),
           -ln.debt,
           ln.description, ln.cp_name, ln.inn_adjust
    FROM ln_src ln
    WHERE ln.debt > 0
)
SELECT
    r.source,
    r.d                                   AS date,
    r.ba_id,
    c.number                              AS ba_number,
    c.currency,
    r.line_id,
    r.journal_line_id,
    r.signed_cur                          AS amount_cur,
    c.rate,
    round(r.signed_cur * c.rate, 2)       AS amount_rub,
    i.code                                AS cf_code,
    COALESCE(i.name, 'Не разнесено')      AS cf_name,
    i.article_code,
    COALESCE(i.article_name, 'Не разнесено') AS article_name,
    i.activity,
    i.code IN ('410200', '420200')        AS is_conversion,
    r.description,
    r.cp_name,
    r.inn_adjust                          AS inn
FROM flows_raw r
LEFT JOIN cash_reval c ON c.ba_id = r.ba_id AND c.date = r.d
LEFT JOIN cf_items i   ON i.id = r.cf_item_id

UNION ALL

-- проводки главной книги по банковским счетам (рубли — как в проводке):
-- ввод остатков → OPENING, корректировки → MANUAL (без статьи → UNALLOC)
SELECT
    CASE
        WHEN g.kind = 'OPENING'      THEN 'OPENING'
        WHEN g.cf_item_id IS NULL    THEN 'UNALLOC'
        ELSE 'MANUAL'
    END,
    g.d,
    g.ba_id,
    c.number,
    c.currency,
    NULL,
    g.journal_line_id,
    g.dt_cur - g.cr_cur,
    c.rate,
    round(g.amount_rub, 2),
    i.code,
    CASE WHEN g.kind = 'OPENING' THEN 'Остаток на начало' ELSE COALESCE(i.name, 'Не разнесено') END,
    i.article_code,
    CASE WHEN g.kind = 'OPENING' THEN 'Остаток на начало' ELSE COALESCE(i.article_name, 'Не разнесено') END,
    i.activity,
    COALESCE(i.code IN ('410200', '420200'), FALSE),
    COALESCE(g.note, g.description),
    NULL,
    NULL
FROM gl_cash g
LEFT JOIN cash_reval c ON c.ba_id = g.ba_id AND c.date = g.d
LEFT JOIN cf_items  i  ON i.id = g.cf_item_id

UNION ALL

-- нет ввода остатков в журнале → остаток из первой выписки (тоже OPENING)
SELECT
    'OPENING',
    c.date,
    c.ba_id,
    c.number,
    c.currency,
    NULL,
    NULL,
    a.opening_stmt,
    c.rate,
    round(c.bb_rub, 2),
    NULL,
    'Остаток на начало',
    NULL,
    'Остаток на начало',
    NULL,
    FALSE,
    'Остаток из первой выписки (нет ввода остатков в журнале)',
    NULL,
    NULL
FROM reval_accounts a
JOIN cash_reval c ON c.ba_id = a.id AND c.date = a.min_date
WHERE NOT a.has_gl_opening
  AND a.opening_stmt <> 0

UNION ALL

-- курсовые разницы на остаток
SELECT
    'FX',
    c.date,
    c.ba_id,
    c.number,
    c.currency,
    NULL,
    NULL,
    0,
    c.rate,
    c.fx_diff_rub,
    NULL,
    'Курсовая разница на остаток',
    NULL,
    'Курсовые разницы',
    NULL,
    FALSE,
    NULL,
    NULL,
    NULL
FROM cash_reval c
WHERE c.fx_diff_rub <> 0;


-- Деятельность и направление — для группировки отчёта.
-- У разнесённых строк берутся из статьи, у остальных — по смыслу строки.
CREATE OR REPLACE TEMP TABLE cash_flow AS
SELECT
    r.* EXCLUDE (activity),

    CASE
        WHEN r.source = 'OPENING' THEN 0
        WHEN r.source = 'FX'      THEN 5
        WHEN r.activity IS NULL   THEN 9
        ELSE r.activity
    END AS activity,

    CASE
        WHEN r.source = 'OPENING' THEN 'Остаток на начало'
        WHEN r.source = 'FX'      THEN 'Курсовые разницы'
        WHEN r.activity = 1       THEN 'Операционная'
        WHEN r.activity = 2       THEN 'Инвестиционная'
        WHEN r.activity = 3       THEN 'Финансовая'
        WHEN r.activity = 4       THEN 'Внутригрупповые'
        ELSE 'Не разнесено'
    END AS activity_name,

    -- направление: у статьи — её, иначе по знаку суммы
    COALESCE(i.direction, CASE WHEN r.amount_rub >= 0 THEN 1 ELSE 2 END) AS direction,
    CASE
        WHEN COALESCE(i.direction, CASE WHEN r.amount_rub >= 0 THEN 1 ELSE 2 END) = 1
        THEN 'Поступления' ELSE 'Выплаты'
    END AS direction_name,

    year(r.date)                  AS year,
    month(r.date)                 AS month,
    strftime(r.date, '%Y-%m')     AS period,

    -- чтобы витрина была самодостаточной (без справочников на сервере)
    b.name                        AS bank_name,
    COALESCE(g.name, r.ba_number) AS account_name
FROM cash_flow_raw r
LEFT JOIN cf_items i                       ON i.code = r.cf_code
LEFT JOIN target_db.treasury_bankaccount a ON a.id = r.ba_id
LEFT JOIN target_db.cp_cp b                ON b.id = a.bank_id
LEFT JOIN target_db.gl_glaccount g         ON g.bank_account_id = r.ba_id;


-- Сверка по каждому счёту: Σ ДДС (с остатком на начало) = остаток на конец, в рублях
CREATE OR REPLACE TEMP TABLE cash_flow_check AS
WITH closing AS (
    SELECT ba_id, number, arg_max(eb_rub, date) AS closing_rub
    FROM cash_reval
    GROUP BY ba_id, number
),
flows AS (
    SELECT
        ba_id,
        sum(amount_rub) FILTER (WHERE source = 'OPENING')  AS opening_rub,
        sum(amount_rub) FILTER (WHERE source <> 'OPENING') AS flows_rub
    FROM cash_flow
    GROUP BY ba_id
)
SELECT
    c.ba_id,
    c.number,
    round(COALESCE(w.opening_rub, 0), 2)  AS opening_rub,
    round(COALESCE(w.flows_rub, 0), 2)    AS flows_rub,
    round(c.closing_rub, 2)               AS closing_rub,
    round(COALESCE(w.opening_rub, 0) + COALESCE(w.flows_rub, 0) - c.closing_rub, 2) AS diff
FROM closing c
LEFT JOIN flows w ON w.ba_id = c.ba_id
ORDER BY abs(COALESCE(w.opening_rub, 0) + COALESCE(w.flows_rub, 0) - c.closing_rub) DESC;
