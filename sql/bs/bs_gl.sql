-- ============================================================
-- bs_gl — одна строка на операцию с точки зрения НАШЕГО счёта
--
-- Нужна временная таблица bs_operations (sql/bs/bs_operations.sql).
--   поступление: наш счёт — получатель, контрагент — плательщик
--   списание:    наш счёт — плательщик, контрагент — получатель
--   перевод между своими: одна строка на каждую сторону
-- ============================================================

CREATE OR REPLACE TEMP TABLE bs_gl AS

WITH unions AS (

    -- поступление
    SELECT
        sid, rr_id, doc_type, doc_number, doc_date,
        ba_number AS ba_account,
        wd_date   AS op_date,
        amount    AS dt,
        0::DECIMAL(18, 2) AS cr,
        inn_payer     AS inn,
        payer_name    AS original_name,
        payer_account AS cp_ba,
        payer_bic     AS cp_bic,
        kbk, intercompany, description
    FROM bs_operations
    WHERE receiver_account = ba_number
      AND intercompany = 0

    UNION ALL

    -- списание
    SELECT
        sid, rr_id, doc_type, doc_number, doc_date,
        ba_number AS ba_account,
        wo_date   AS op_date,
        0::DECIMAL(18, 2) AS dt,
        amount    AS cr,
        inn_receiver     AS inn,
        receiver_name    AS original_name,
        receiver_account AS cp_ba,
        receiver_bic     AS cp_bic,
        kbk, intercompany, description
    FROM bs_operations
    WHERE payer_account = ba_number
      AND intercompany = 0

    UNION ALL

    -- перевод между своими счетами
    SELECT
        sid, rr_id, doc_type, doc_number, doc_date,
        ba_number AS ba_account,
        doc_date  AS op_date,
        CASE WHEN receiver_account = ba_number THEN amount ELSE 0::DECIMAL(18, 2) END AS dt,
        CASE WHEN payer_account    = ba_number THEN amount ELSE 0::DECIMAL(18, 2) END AS cr,
        COALESCE(inn_receiver, inn_payer)         AS inn,
        COALESCE(receiver_name, payer_name)       AS original_name,
        COALESCE(receiver_account, payer_account) AS cp_ba,
        COALESCE(receiver_bic, payer_bic)         AS cp_bic,
        kbk, intercompany, description
    FROM bs_operations
    WHERE intercompany = 1
)

SELECT
    *,
    NULLIF(regexp_extract(description, '\{(VO\d+)\}', 1), '') AS vo_code,
    CASE WHEN length(cp_ba) = 20 THEN left(cp_ba, 5) END      AS ba_resolver
FROM unions
;
