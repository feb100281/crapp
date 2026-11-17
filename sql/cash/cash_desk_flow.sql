-- ============================================================
-- Касса → строки ДДС в том же виде, что банковские (cash_flow_raw в sql/bs/cash_flow.sql),
-- чтобы потом сделать  SELECT … FROM cash_flow_raw UNION ALL BY NAME SELECT … FROM cash_desk_flow.
--
-- Нужно: ATTACH базы как target_db. Плейсхолдер __CASH_DIR__ — папка
-- data/parquet/cash (cash_desk.parquet + cash_desk_files.parquet, команда cash_desk).
--
-- Знак amount_rub: + приход в кассу, − расход. Валюта всегда RUB, курс 1.
-- Чего пока не знаем — NULL: ba_id (касса не банковский счёт), статьи ДДС
-- по кассовым операциям (cf_code / activity — потом резолвером по корр. счёту
-- и статье 1С), ИНН.
--
-- source:
--   CASH_OPENING  — сальдо на начало первого файла карточки 50
--   CASH          — строки карточки 50, КРОМЕ сдачи в банк (корр. 57.01)
--   CASH_TO_BANK  — сдача наличных в банк: берём НЕ из 1С, а из банковских
--                   выписок (взносы на наш счёт), со знаком минус → статья 420500.
--                   В 1С взносы часто сгруппированы одной суммой на день,
--                   в банке — по каждому взносу; даты могут расходиться на 1–3 дня.
--
-- Разница между нашим остатком (этот расчёт) и сальдо 1С = «деньги в пути»:
-- 1С списала из кассы на 57.01, а банк ещё не зачислил (+), или наоборот (−).
-- Хвост, который не закрывается, — косяк учёта. См. cash_desk_check.sql.
-- ============================================================

-- взносы наличных на наши счета по банковским выпискам
--   20208                 — Альфа: «Внес.ср. ООО "КОСМО" ч-з ТУ …» (касса банка)
--   описание «Внес.ср»    — Альфа, те же взносы через транзитный 30233
--   30232 «внесенной … выручки» — ГПБ: «зачисление внесенной на карту выручки через Устройства Банка»
CREATE OR REPLACE TEMP TABLE bank_cash_in AS
SELECT
    l.id                AS line_id,
    l.op_date::DATE     AS d,
    l.amount::DOUBLE    AS amount,
    a.number            AS ba_number,
    l.description
FROM target_db.treasury_bsline l
JOIN target_db.treasury_bankaccount a ON a.id = l.ba_account_id
WHERE l.direction = 1
  AND (
        l.cp_ba LIKE '20208%'
     OR l.description ILIKE '%Внес.ср%'
     OR (l.cp_ba LIKE '30232%' AND l.description ILIKE '%внесенн%выручк%')
  );


CREATE OR REPLACE TEMP TABLE cash_desk_flow AS
WITH cash AS (
    SELECT * FROM read_parquet('__CASH_DIR__/cash_desk.parquet')
),
files AS (
    SELECT * FROM read_parquet('__CASH_DIR__/cash_desk_files.parquet')
),
item AS (           -- статья «Выдача наличных из кассы для сдачи в банк»
    SELECT
        i.code, i.name,
        COALESCE(p.code, i.code) AS article_code,
        COALESCE(p.name, i.name) AS article_name,
        i.activity
    FROM target_db.treasury_cfitem i
    LEFT JOIN target_db.treasury_cfitem p ON p.id = i.parent_id
    WHERE i.code = '420500'
)

-- сальдо на начало
SELECT
    'CASH_OPENING'                      AS source,
    min(f.date_from)                    AS date,
    NULL::BIGINT                        AS ba_id,
    arg_min(f.cash_account, f.date_from) AS ba_number,
    'RUB'                               AS currency,
    NULL::BIGINT                        AS line_id,
    NULL::BIGINT                        AS journal_line_id,
    arg_min(f.opening, f.date_from)::DOUBLE AS amount_cur,
    1.0                                 AS rate,
    arg_min(f.opening, f.date_from)::DOUBLE AS amount_rub,
    NULL                                AS cf_code,
    'Остаток на начало'                 AS cf_name,
    NULL                                AS article_code,
    'Остаток на начало'                 AS article_name,
    NULL::INTEGER                       AS activity,
    FALSE                               AS is_conversion,
    'Сальдо на начало карточки 50 (' || arg_min(f.source_file, f.date_from) || ')' AS description,
    NULL                                AS cp_name,
    NULL                                AS inn
FROM files f

UNION ALL

-- строки карточки 50, кроме сдачи в банк
SELECT
    'CASH',
    c.date,
    NULL,
    c.cash_account,
    'RUB',
    NULL,
    NULL,
    CASE WHEN c.direction = 1 THEN 1 ELSE -1 END * c.amount::DOUBLE,
    1.0,
    CASE WHEN c.direction = 1 THEN 1 ELSE -1 END * c.amount::DOUBLE,
    NULL,                                   -- статья — потом (резолвер по корр. счёту / статье 1С)
    'Не разнесено',
    NULL,
    'Не разнесено',
    NULL,
    FALSE,
    concat_ws(' · ',
        trim(c.doc_type || ' ' || c.doc_number),
        'корр. ' || c.corr_account,
        NULLIF(c.cf_item_1c, ''),
        NULLIF(c.doc_comment, '')),
    NULLIF(NULLIF(c.corr_subconto1, ''), '<...>'),
    NULL
FROM cash c
WHERE c.corr_account <> '57.01'

UNION ALL

-- сдача в банк — по банковским выпискам, со знаком минус
SELECT
    'CASH_TO_BANK',
    b.d,
    NULL,
    (SELECT arg_min(cash_account, date_from) FROM files),
    'RUB',
    b.line_id,
    NULL,
    -b.amount,
    1.0,
    -b.amount,
    i.code,
    COALESCE(i.name, 'Не разнесено'),
    i.article_code,
    COALESCE(i.article_name, 'Не разнесено'),
    i.activity,
    FALSE,
    'Сдано в банк на ' || b.ba_number || ' · ' || b.description,
    'ООО «Космо»',
    NULL
FROM bank_cash_in b
LEFT JOIN item i ON TRUE;
