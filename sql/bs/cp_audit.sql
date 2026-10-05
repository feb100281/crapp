-- ============================================================
-- cp_audit.sql — роли контрагента и проверка ИНН по bs_gl
--
-- Только чтение: создаёт TEMP-таблицы, в базу ничего не пишет.
-- Запускать в той же сессии DuckDB ПОСЛЕ sql/attached.sql
-- и после создания statements / bs_operations / bs_gl
-- (см. sql/trash/bs_pipeline.sql). Нужны поля bs_gl:
--   ba_account, cp_ba, cp_bic, inn, original_name, kbk,
--   description, vo_code, intercompany, dt, cr, rr_id
-- ============================================================


-- ------------------------------------------------------------
-- 0. Макросы: цифра ИНН и контрольные суммы
-- ------------------------------------------------------------
CREATE OR REPLACE TEMP MACRO dg(s, i) AS CAST(substr(s, i, 1) AS INTEGER);

CREATE OR REPLACE TEMP MACRO inn_valid(s) AS
    CASE
        WHEN s IS NULL THEN NULL
        WHEN regexp_full_match(s, '\d{10}') THEN
            ((2*dg(s,1) + 4*dg(s,2) + 10*dg(s,3) + 3*dg(s,4) + 5*dg(s,5)
              + 9*dg(s,6) + 4*dg(s,7) + 6*dg(s,8) + 8*dg(s,9)) % 11) % 10 = dg(s,10)
        WHEN regexp_full_match(s, '\d{12}') THEN
            ((7*dg(s,1) + 2*dg(s,2) + 4*dg(s,3) + 10*dg(s,4) + 3*dg(s,5)
              + 5*dg(s,6) + 9*dg(s,7) + 4*dg(s,8) + 6*dg(s,9) + 8*dg(s,10)) % 11) % 10 = dg(s,11)
            AND
            ((3*dg(s,1) + 7*dg(s,2) + 2*dg(s,3) + 4*dg(s,4) + 10*dg(s,5)
              + 3*dg(s,6) + 5*dg(s,7) + 9*dg(s,8) + 4*dg(s,9) + 6*dg(s,10)
              + 8*dg(s,11)) % 11) % 10 = dg(s,12)
        ELSE FALSE
    END;


-- ------------------------------------------------------------
-- 1. Справочники: наши ИНН и ИНН банков
-- ------------------------------------------------------------
-- наши юрлица (владельцы счетов)
CREATE OR REPLACE TEMP TABLE own_inn AS
    SELECT DISTINCT inn::TEXT AS inn
    FROM target_db.cp_gr
    WHERE inn IS NOT NULL;

-- банк, в котором открыт каждый наш счёт
CREATE OR REPLACE TEMP TABLE own_bank AS
    SELECT
        a.number::TEXT AS ba_number,
        c.id           AS bank_cp_id,
        c.inn::TEXT    AS bank_inn,
        c.name         AS bank_name
    FROM target_db.treasury_bankaccount a
    LEFT JOIN target_db.cp_cp c ON c.id = a.bank_id;


-- ------------------------------------------------------------
-- 2. Роль контрагента по каждой строке bs_gl
--
--   ic       внутригрупповой перевод
--   budget   КБК (админ. не 000) или казначейский счёт 03xxx
--   bank     внутренний счёт банка: 42/43/44/45/47/60/70, 202xx, 302xx, 30101
--   foreign  ВЭД: есть {VO…}, счёт не 20 знаков, корсчета 3011x/3030x
--   client   счёт клиента другого банка (40xxx)
--   unknown  всё остальное
--
-- Порядок важен: комиссия с {VO80150} на 47423 — это bank, а не foreign.
-- ------------------------------------------------------------
CREATE OR REPLACE TEMP TABLE cp_roles AS
    WITH base AS (
        SELECT
            g.*,
            NULLIF(regexp_replace(COALESCE(g.inn, ''), '\D', '', 'g'), '') AS inn_clean,
            left(g.cp_ba, 5) AS p5,
            CASE WHEN g.dt <> 0 THEN 'in' ELSE 'out' END AS direction
        FROM bs_gl g
    )
    SELECT
        b.*,
        CASE
            WHEN b.intercompany::INTEGER = 1                              THEN 'ic'
            WHEN (b.kbk ~ '^\d{20}$' AND left(b.kbk, 3) <> '000')
              OR left(b.cp_ba, 2) = '03'                                     THEN 'budget'
            WHEN left(b.cp_ba, 2) IN ('42', '43', '44', '45', '47', '60', '70')
              OR left(b.cp_ba, 3) IN ('202', '302')
              OR b.p5 = '30101'                                              THEN 'bank'
            WHEN b.vo_code IS NOT NULL
              OR b.cp_ba IS NULL
              OR length(b.cp_ba) <> 20
              OR left(b.cp_ba, 4) IN ('3011', '3030')                        THEN 'foreign'
            WHEN left(b.cp_ba, 2) = '40'                                     THEN 'client'
            ELSE 'unknown'
        END AS role,
        inn_valid(b.inn_clean) AS inn_ok,
        b.inn_clean IN (SELECT inn FROM own_inn) AS inn_is_own,
        ob.bank_inn  AS own_bank_inn,
        ob.bank_name AS own_bank_name
    FROM base b
    LEFT JOIN own_bank ob ON ob.ba_number = b.ba_account;

-- ИНН банков: из справочника + выведенные из данных (ИНН на внутренних счетах банка)
CREATE OR REPLACE TEMP TABLE bank_inn AS
    SELECT bank_inn AS inn FROM own_bank WHERE bank_inn IS NOT NULL
    UNION
    SELECT DISTINCT inn_clean FROM cp_roles WHERE role = 'bank' AND inn_clean IS NOT NULL;

ALTER TABLE cp_roles ADD COLUMN inn_is_bank BOOLEAN;
UPDATE cp_roles SET inn_is_bank = inn_clean IN (SELECT inn FROM bank_inn);


-- ============================================================
-- ОТЧЁТЫ
-- ============================================================

-- A. Сколько операций в какой роли и направлении
SELECT
    role,
    direction,
    count(*)                       AS ops,
    round(sum(dt) / 1e6, 1)        AS dt_mln,
    round(sum(cr) / 1e6, 1)        AS cr_mln
FROM cp_roles
GROUP BY role, direction
ORDER BY role, direction;


-- B. Аномалии ИНН по ролям
SELECT
    role,
    count(*)                                                        AS ops,
    count(*) FILTER (WHERE inn_clean IS NULL)                       AS no_inn,
    count(*) FILTER (WHERE inn_clean IS NOT NULL AND NOT inn_ok)    AS bad_checksum,
    count(*) FILTER (WHERE inn_is_own AND role <> 'ic')             AS own_inn_not_ic,
    count(*) FILTER (WHERE inn_is_bank AND role NOT IN ('bank'))    AS bank_inn_not_bank_role
FROM cp_roles
GROUP BY role
ORDER BY ops DESC;


-- C. ИНН «Космо» у контрагента вне intercompany (депозиты, кредиты, овердрафт)
--    должен быть банк нашего счёта
SELECT
    role,
    p5,
    direction,
    own_bank_name,
    count(*)                     AS ops,
    round(sum(dt + cr) / 1e6, 1) AS turnover_mln,
    any_value(description)       AS sample_description
FROM cp_roles
WHERE inn_is_own AND role <> 'ic'
GROUP BY role, p5, direction, own_bank_name
ORDER BY ops DESC;


-- D. Роль bank / budget / foreign, но ИНН не банка и не бюджета —
--    кто вообще стоит в поле ИНН на внутренних счетах
SELECT
    role,
    p5,
    inn_clean,
    inn_is_bank,
    count(*)                                  AS ops,
    list(DISTINCT left(original_name, 40))[:3] AS names
FROM cp_roles
WHERE role IN ('bank', 'budget')
GROUP BY role, p5, inn_clean, inn_is_bank
ORDER BY ops DESC
LIMIT 40;


-- E. Клиентские счета (role = client): один cp_ba — несколько ИНН.
--    Если такие есть, ИНН нельзя использовать как ключ и cp_ba надёжнее.
SELECT
    cp_ba,
    cp_bic,
    count(DISTINCT inn_clean)                           AS inn_cnt,
    count(*)                                            AS ops,
    list(DISTINCT inn_clean)                            AS inns,
    list(DISTINCT left(original_name, 40))[:4]          AS names
FROM cp_roles
WHERE role = 'client' AND inn_clean IS NOT NULL
GROUP BY cp_ba, cp_bic
HAVING count(DISTINCT inn_clean) > 1
ORDER BY ops DESC
LIMIT 20;


-- F. Клиентские ИНН: один ИНН — много разных названий (грубая нормализация)
WITH n AS (
    SELECT
        inn_clean,
        cp_ba,
        left(regexp_replace(upper(original_name), '[^А-ЯЁA-Z0-9]', '', 'g'), 24) AS nm,
        original_name
    FROM cp_roles
    WHERE role = 'client' AND inn_clean IS NOT NULL
)
SELECT
    inn_clean,
    count(DISTINCT nm)      AS name_cnt,
    count(DISTINCT cp_ba)   AS ba_cnt,
    count(*)                AS ops,
    list(DISTINCT left(original_name, 40))[:4] AS names
FROM n
GROUP BY inn_clean
HAVING count(DISTINCT nm) > 1
ORDER BY ops DESC
LIMIT 20;


-- G. Роль unknown: что не попало ни в один класс (по 5 знакам)
SELECT
    p5,
    direction,
    count(*)                       AS ops,
    round(sum(dt + cr) / 1e6, 1)   AS turnover_mln,
    any_value(left(description, 90)) AS sample_description
FROM cp_roles
WHERE role = 'unknown'
GROUP BY p5, direction
ORDER BY ops DESC
LIMIT 30;
