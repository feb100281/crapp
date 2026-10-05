-- /Users/pavelustenko/Library/CloudStorage/Dropbox/Remark_app/COSMO/2026-09-10/Cash_2026-09-10.xlsx 
-- /Users/pavelustenko/Library/CloudStorage/Dropbox/Remark_app/COSMO/2026-09-10/Orders_2026-09-10.xlsx 
-- /Users/pavelustenko/Library/CloudStorage/Dropbox/Remark_app/COSMO/2026-09-10/Sales_2026-09-10.xlsx 
-- /Users/pavelustenko/Library/CloudStorage/Dropbox/Remark_app/COSMO/2026-09-10/Stocks_2026-09-10.xlsx


select 
CURRENT_DATE as date_uploaded,
"GUID_ЗК"::text as guid_order,
"GUID_Номенклатуры"::text as guid_namber,
"GUID_Характеристики"::text as guid_character,
"Артикул"::text as article,
try_strptime("Дата и время изменения", '%d.%m.%Y %H:%M:%S') AS date_change,
TRY_CAST("Итоговая сумма" as decimal(12,2)) as amount_final,
TRY_CAST("Итоговая цена" as decimal(12,2)) as amount_final,
"Клиент"::text as client_name,
TRY_CAST("Кол." as decimal(12,2)) as qty,
"Менеджер"::text as manager,
"Номер Заказа"::text as order_number,
"Номер на сайте"::text as numder_on_site,
"Подразделение"::text as department,
"ПричинаОтмены"::text as cancel_reason,
"РабочееНаименование"::text as working_name,
"Склад"::text as warehouse_name,
"Статус"::text as order_status,
TRY_CAST("СуммаАвтоСкидки" as decimal(12,2)) as amount_discount_auto,
TRY_CAST("СуммаАгентскойСкидки" as decimal(12,2)) as amount_discount_agent,
TRY_CAST("СуммаРучнойСкидки" as decimal(12,2)) as amount_discount_manual,
"Тип операции"::text as oper_type,
TRY_CAST("Цена полная" as decimal(12,2)) as price_full,
"Штрихкод"::text as barcode,
TRY_CAST("% Авто скидки" as decimal(12,2)) as prc_discount_auto,
TRY_CAST("% АгентскойСкидки" as decimal(12,2)) as prc_discount_agent,
TRY_CAST("% РучнойСкидки" as decimal(12,2)) as prc_discount_manual

FROM
read_xlsx(
    '/Users/pavelustenko/Library/CloudStorage/Dropbox/Remark_app/COSMO/2026-09-10/Orders_2026-09-10.xlsx',
    range = 'B2:BB100000',
    header = true    
    )
where "GUID_ЗК" is not null;

select 
"GUID_ЗК"::text as guid_order,
try_strptime("Дата операции", '%d.%m.%y') AS date_trasaction,
"Тип операции"::text as transaction_type,
"Назначение операции"::text as description,
"Касса"::text as cashdesk,
"Номер документа"::text as doc_number,
TRY_CAST(
    REPLACE(
        regexp_replace(
            "Сумма операции",
            '[^0-9,-]',
            '',
            'g'
        ),
        ',',
        '.'
    )
    AS DECIMAL(18, 2)
) AS amount,
"Подразделение"::text as department_name,
"Регистратор"::text as register

from read_xlsx(
    '/Users/pavelustenko/Library/CloudStorage/Dropbox/Remark_app/COSMO/2026-09-10/Cash_2026-09-10.xlsx',
    range = 'B2:R100000',
    header = true
    )
WHERE "Регистратор" is not null;

select *  

FROM read_json_auto(
        '/Users/pavelustenko/cr/data/json/bs/bs.jsonl',
        format = 'newline_delimited',
        union_by_name = true,
        sample_size = -1
    )

;
create or replace temp table ba as
select DISTINCT
"ba_number",
case 
when  CONTAINS(LOWER(source_file),'альфа') then 'АЛЬФА-БАНК'
when  CONTAINS(source_file,'АБ') then 'АЛЬФА-БАНК'
when  CONTAINS(LOWER(source_file),'газп') then 'ГАЗПРОМБАНК'
when  CONTAINS(LOWER(source_file),'отп') then 'ОТП БАНК'
when  CONTAINS(LOWER(source_file),'финам') then 'ФИНАМ БАНК'
when  CONTAINS(LOWER(source_file),'втб') then 'ВТБ'
when  CONTAINS(LOWER(source_file),'бспб') then 'СПБ БАНК'
end as bank_name,

case when LOWER(source_file) like '%транз%' then 'Транзитный' else 'Основной' end as account_type,
'....'||"ba_number"[-6:] as short_acc
from read_parquet('data/parquet/banks/statement.parquet')
;

create or replace temp table bs as

select 
* 
from read_parquet('data/parquet/banks/statement.parquet')
;

with intercompany_check as (
select
s.*,
case when 
s.receiver_account in (select "ba_number" from ba) and 
s.payer_account in (select "ba_number" from ba) then 1 else 0 end as intercompany
from read_parquet('data/parquet/bs/bs.parquet') s
),

external_ as (
select 
b.bank_name || b.short_acc || ' (' || left(account_type,2)||')' as account_name,
t.statement_id,
t.doc_type,
t.doc_number,
t.doc_date,
t.wo_date,
t.wd_date,
t.amount as dt,
0 as cr,
t.inn_payer as inn,
{
    'original_name': t.payer_name,
    'payer_account': t.payer_account
} as original_details,
intercompany,
t.description

from intercompany_check t
left join ba b on b.ba_number = t.receiver_account
where intercompany = false  and account_name is not null

union all

select 
b.bank_name || b.short_acc || ' (' || left(account_type,2)||')' as account_name,
t.statement_id,
t.doc_type,
t.doc_number,
t.doc_date,
t.wo_date,
t.wd_date,
0 as dt,
t.amount as cr,

t.inn_receiver as inn,
{
    'original_name': t.receiver_name,
    'payer_account': t.receiver_account
} as original_details,
intercompany,
t.description

from intercompany_check t
left join ba b on b.ba_number = t.payer_account
where intercompany = false  and account_name is not null
)

union all

select
b.bank_name || b.short_acc || ' (' || left(account_type,2)||')' as account_name,
t.statement_id,
t.doc_type,
t.doc_number,
t.doc_date,
t.wo_date,
t.wd_date,
case 
when t.receiver_account = b.ba_number then t.amount else 0 end as dt,
case 
when t.payer_account = b.ba_number then t.amount else 0 end as cr
COALESCE(t.inn_receiver, t.inn_payer) as inn,
{
    'original_name': t.receiver_name,
    'payer_account': t.receiver_account
} as original_details,
intercompany,
t.description
from intercompany_check t
left join bs b on b.id = t.statement_id
left join ba ba on ba.ba_number = b.ba_number;

create or replace temp table cash_gl as
 
WITH intercompany_check AS (

    SELECT
        s.*,

        CASE
            WHEN s.receiver_account IN (
                SELECT ba_number
                FROM ba
            )
            AND s.payer_account IN (
                SELECT ba_number
                FROM ba
            )
            THEN 1
            ELSE 0
        END AS intercompany

    FROM read_parquet('data/parquet/bs/bs.parquet') s
),

external_ AS (

    -- =========================================================
    -- Входящие внешние платежи
    -- Деньги пришли НА наш счет
    -- Контрагент = плательщик
    -- =========================================================

    SELECT
        b.bank_name
            || b.short_acc
            || ' ('
            || left(b.account_type, 2)
            || ')' AS account_name,

        t.statement_id,
        t.doc_type,
        t.doc_number,
        t.doc_date,
        t.wo_date,
        t.wd_date,

        t.amount AS dt,
        0 AS cr,

        t.inn_payer AS inn,
        t.payer_name as original_name,
        t.payer_account as cp_ba,


        t.intercompany,
        t.description

    FROM intercompany_check t

    LEFT JOIN ba b
        ON b.ba_number = t.receiver_account

    WHERE
        t.intercompany = false
        AND b.ba_number IS NOT NULL


    UNION ALL


    -- =========================================================
    -- Исходящие внешние платежи
    -- Деньги ушли С нашего счета
    -- Контрагент = получатель
    -- =========================================================

    SELECT
        b.bank_name
            || b.short_acc
            || ' ('
            || left(b.account_type, 2)
            || ')' AS account_name,

        t.statement_id,
        t.doc_type,
        t.doc_number,
        t.doc_date,
        t.wo_date,
        t.wd_date,

        0 AS dt,
        t.amount AS cr,

        t.inn_receiver AS inn,

        t.receiver_name as original_name,
        t.receiver_account as cp_ba,

       

        t.intercompany,
        t.description

    FROM intercompany_check t

    LEFT JOIN ba b
        ON b.ba_number = t.payer_account

    WHERE
        t.intercompany = false
        AND b.ba_number IS NOT NULL
)


-- =============================================================
-- Внешние операции
-- =============================================================

SELECT *
FROM external_


UNION ALL


-- =============================================================
-- Внутренние операции
--
-- Для intercompany нужно определить КОНКРЕТНЫЙ счет выписки.
-- statement_id -> bs -> ba
--
-- Если счет выписки = receiver_account -> DT
-- Если счет выписки = payer_account    -> CR
-- =============================================================

SELECT
    ba.bank_name
        || ba.short_acc
        || ' ('
        || left(ba.account_type, 2)
        || ')' AS account_name,

    t.statement_id,
    t.doc_type,
    t.doc_number,
    t.doc_date,
    t.wo_date,
    t.wd_date,

    CASE
        WHEN t.receiver_account = ba.ba_number
            THEN t.amount
        ELSE 0
    END AS dt,

    CASE
        WHEN t.payer_account = ba.ba_number
            THEN t.amount
        ELSE 0
    END AS cr,

    COALESCE(
        t.inn_receiver,
        t.inn_payer
    ) AS inn,
    CASE
        WHEN t.receiver_account = ba.ba_number
            THEN t.payer_name
        ELSE t.receiver_name
    END as original_name,

    CASE
        WHEN t.receiver_account = ba.ba_number
            THEN t.payer_account
        ELSE t.receiver_account
    END as payer_account,


    t.intercompany,
    t.description

FROM intercompany_check t

LEFT JOIN bs s
    ON s.id = t.statement_id

LEFT JOIN ba
    ON ba.ba_number = s.ba_number

WHERE
    t.intercompany = true
    AND ba.ba_number IS NOT NULL;

SELECT 
inn,
len(LIST(DISTINCT cp_ba)) as qty_ba,
LIST(DISTINCT cp_ba) as ba_list,
list(DISTINCT
    {
        'original_name': original_name,   
        'inn': inn,
        'cp_ba': cp_ba    
    }
) as original_names,
list(DISTINCT
    {
        'description': description,       
    }
) as original_description,

from cash_gl
GROUP BY inn
;

select 
original_name,
list(distinct cp_ba) as bas,
len(list(distinct cp_ba)) as ln,
list(distinct inn)
from cash_gl
GROUP BY original_name;

select * from cash_gl;

WITH pre_trasform AS (
    SELECT 
        cp_ba,
        list(DISTINCT original_name) AS names,
        list(DISTINCT 
        {
            'original_name': original_name,
            'inn':inn
        }
        ) AS names_inn,
        len(list(DISTINCT original_name)) AS ln,
        
        list(DISTINCT description) AS description,
        {
            'names': list(DISTINCT original_name),
            'inns': list(DISTINCT inn)
        } as str_data,


        sum(dt) AS dt,
        sum(cr) AS cr
    FROM cash_gl
    WHERE cp_ba NOT IN (
        SELECT ba_number
        FROM ba
    )
    and intercompany = 0
    GROUP BY cp_ba
)

SELECT
    *,
    list_distinct(
        list_filter(
            list_transform(
                names,
                x -> regexp_extract(x, '(?:ИНН\s+|^)([0-9]+)', 1)
            ),
            x -> x <> ''
        )
    ) AS inns,
    len(
        list_distinct(
        list_filter(
            list_transform(
                names,
                x -> regexp_extract(x, '(?:ИНН\s+|^)([0-9]+)', 1)
            ),
            x -> x <> ''
        )
    ) 
    ) inn_count
FROM pre_trasform;

select DISTINCT
source_file,
ba_number,
substring(ba_number, 6, 3)
from read_parquet('data/parquet/banks/statement.parquet');

select DISTINCT
substring(receiver_account, 6, 3)
from read_parquet('data/parquet/bs/bs.parquet');





