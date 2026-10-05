-- ============================================================
-- Новые строки bs_gl_adj → treasury_bsline
--
-- Только ДОБАВЛЯЕТ: строки, чей rr_id уже есть в базе, не трогаются.
-- desc_pattern — назначение, где любое «слово» с цифрой
-- (номер, сумма, дата, УТ-523, UUID заказа) заменено на #.
-- ============================================================

INSERT INTO target_db.treasury_bsline BY NAME

SELECT
    g.rr_id,
    st.id AS statement_id,
    a.id  AS ba_account_id,

    g.doc_type,
    g.doc_number,
    g.doc_date,
    g.op_date,

    g.direction,
    g.amount,
    g.dt,
    g.cr,
    (g.intercompany = 1)::INTEGER AS intercompany,

    g.inn,
    g.inn_adjust,
    g.original_name AS cp_name,
    g.cp_ba,
    g.cp_bic,

    g.ba_resolver,
    g.kbk,
    g.vo_code,
    g.description,

    trim(regexp_replace(regexp_replace(regexp_replace(
        coalesce(g.description, ''),
        '[0-9A-Za-zА-Яа-яЁё]*[0-9][0-9A-Za-zА-Яа-яЁё/.,:№-]*', '#', 'g'),
        '#(\s*#)+', '#', 'g'),
        '\s+', ' ', 'g')) AS desc_pattern,

    g.fee_cr,
    COALESCE(g.fee_withheld, FALSE)::INTEGER AS fee_withheld,
    g.debt_cr,
    g.gross_dt,

    g.vat_rate,
    g.vat_amount,
    COALESCE(g.vat_free, FALSE)::INTEGER AS vat_free,
    g.vat_check::INTEGER AS vat_check,

    strftime(localtimestamp, '%Y-%m-%d %H:%M:%S') AS imported_at

FROM bs_gl_adj g

LEFT JOIN target_db.treasury_statement st
       ON st.sid = g.sid

LEFT JOIN target_db.treasury_bankaccount a
       ON a.number = g.ba_account

WHERE NOT EXISTS (
    SELECT 1
    FROM target_db.treasury_bsline b
    WHERE b.rr_id = g.rr_id
)

QUALIFY row_number() OVER (PARTITION BY g.rr_id ORDER BY g.op_date) = 1
;
