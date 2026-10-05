-- ============================================================
-- bs_gl_adj — bs_gl + поправки, которые нужны до разноски
--
-- Запускать после bs_gl (sql/trash/bs_pipeline.sql) и attached.sql.
--
-- Добавляет колонки:
--   inn_adjust      ИНН контрагента; «Космо» на счетах банка → ИНН банка
--   direction       1 — поступление (dt), 2 — списание (cr)
--                   те же коды, что Direction у статей ДДС
--   amount          сумма строки = dt или cr (без знака)
--
--   Колонки ниже бывают ТОЛЬКО у поступлений (direction = 1):
--   fee_cr          комиссия банка из назначения («Комиссия банка N»)
--                   — по смыслу это СПИСАНИЕ внутри поступления
--   fee_withheld    комиссия удержана из поступления (эквайринг по картам)
--                   СБП — FALSE: там комиссия списывается ОТДЕЛЬНОЙ строкой
--                   (проверено: 1045 из 1045 платежей СБП)
--   debt_cr         «с уч. гаш. долга-N» — удержано в счёт долга (ГПБ),
--                   тоже СПИСАНИЕ внутри поступления
--   gross_dt        сколько заплатил покупатель (ПОСТУПЛЕНИЕ):
--                   dt + удержанная комиссия + удержанный долг
--
--   При разноске поступление с удержанной комиссией раскладывается так:
--       + gross_dt  → статья выручки       (direction 1)
--       − fee_cr    → комиссии банка        (direction 2)
--       − debt_cr   → погашение долга       (direction 2)
--   gross_dt − fee_cr − debt_cr = dt, то есть сумма строки сходится.
--   vat_rate        ставка НДС из назначения (20, 22, 10 …)
--   vat_amount      сумма НДС из назначения
--   vat_free        в назначении «без НДС / НДС не облагается»
--   vat_check       TRUE — сумма НДС сходится со ставкой,
--                   FALSE — косяк в назначении, NULL — проверять нечем
--
-- НДС после «Комиссия банка …» относится к комиссии, а не к платежу,
-- поэтому НДС ищем только в тексте ДО слов «Комиссия банка».
-- ============================================================


-- число из текста: «3 369.83», «9.665,00», «7723-33», «1716,67» → DECIMAL
CREATE OR REPLACE TEMP MACRO to_num(x) AS
    TRY_CAST(
        replace(
            regexp_replace(
                CASE
                    WHEN contains(replace(replace(x, ' ', ''), chr(160), ''), '.')
                     AND contains(replace(replace(x, ' ', ''), chr(160), ''), ',')
                    THEN replace(replace(replace(replace(x, ' ', ''), chr(160), ''), '.', ''), ',', '.')
                    ELSE replace(replace(x, ' ', ''), chr(160), '')
                END,
                '[,-](\d{1,2})$', '.\1'
            ),
            ',', '.'
        ) AS DECIMAL(18, 2)
    );


CREATE OR REPLACE TEMP TABLE bs_gl_adj AS
WITH base AS (
    SELECT
        t.*,

        CASE
            WHEN t.inn IN (SELECT inn FROM target_db.cp_gr)
             AND t.intercompany = 0
             AND left(t.cp_ba, 2) <> '40'
            THEN b.inn
            ELSE t.inn
        END AS inn_adjust,

        CASE WHEN t.dt > 0 THEN 1 ELSE 2 END AS direction,
        (t.dt + t.cr)::DECIMAL(18, 2)  AS amount,
        t.dt > 0                       AS is_in,
        COALESCE(t.description, '')    AS d

    FROM bs_gl t
    LEFT JOIN target_db.treasury_bankaccount a ON a.number = t.ba_account
    LEFT JOIN target_db.cp_cp b               ON b.id = a.bank_id
),

-- комиссия и долг — только у поступлений
fee AS (
    SELECT
        *,
        CASE WHEN is_in THEN to_num(NULLIF(regexp_extract(
            d, '(?i)комиссия банка\s*[:=]?\s*(\d[\d ]*(?:[.,]\d{1,2})?)', 1), ''))
        END AS fee_cr,

        CASE WHEN is_in THEN to_num(NULLIF(regexp_extract(
            d, '(?i)гаш\.?\s*долга\s*-?\s*(\d[\d ]*(?:[.,]\d{1,2})?)', 1), ''))
        END AS debt_cr,

        -- текст для поиска НДС платежа: всё, что ДО «Комиссия банка»
        CASE
            WHEN strpos(lower(d), 'комиссия банка') > 0
            THEN left(d, strpos(lower(d), 'комиссия банка') - 1)
            ELSE d
        END AS d_vat
    FROM base
),

vat AS (
    SELECT
        *,
        TRY_CAST(replace(NULLIF(regexp_extract(
            d_vat, '(?i)НДС[^0-9%]{0,15}?(\d{1,2}(?:[.,]\d+)?)\s*%', 1), ''), ',', '.')
            AS DECIMAL(5, 2)) AS vat_rate,

        -- группа 1 — число после «НДС [(20%)] [-=:]», группа 2 — «%» сразу за числом
        -- (если «%» есть, это ставка, а не сумма: «НДС 22%.»)
        to_num(NULLIF(regexp_extract(d_vat,
            '(?i)НДС\s*(?:\(?\s*\d{1,2}(?:[.,]\d+)?\s*%\s*\)?)?\s*[-=:]?\s*(?:сумма\s*)?(\d{1,3}(?:[ .]\d{3})+(?:[.,-]\d{1,2})?|\d+(?:[.,-]\d{1,2})?)(\s*%)?',
            1), '')) AS vat_raw,

        regexp_extract(d_vat,
            '(?i)НДС\s*(?:\(?\s*\d{1,2}(?:[.,]\d+)?\s*%\s*\)?)?\s*[-=:]?\s*(?:сумма\s*)?(\d{1,3}(?:[ .]\d{3})+(?:[.,-]\d{1,2})?|\d+(?:[.,-]\d{1,2})?)(\s*%)?',
            2) <> '' AS vat_raw_is_pct
    FROM fee
)

SELECT
    * EXCLUDE (d, d_vat, vat_raw, vat_raw_is_pct, is_in, vat_amount_tmp),

    vat_amount_tmp AS vat_amount,

    vat_amount_tmp IS NULL
      AND vat_rate IS NULL
      AND regexp_matches(d_vat,
          '(?i)(без\s*(налога\s*)?\(?\s*НДС|НДС\s*не\s*обл|НДС\s*нет|без\s*налога|НДС\s*не\s*предусм)')
        AS vat_free,

    CASE
        WHEN vat_amount_tmp IS NOT NULL AND vat_rate IS NOT NULL
        THEN abs(vat_amount_tmp - amount * vat_rate / (100 + vat_rate))
             <= greatest(1, amount * 0.002)
    END AS vat_check

FROM (
    SELECT
        *,
        fee_cr IS NOT NULL
          AND fee_cr < amount
          AND d NOT ILIKE '%СБП%'                          AS fee_withheld,

        CASE WHEN direction = 1 THEN
            amount
              + CASE WHEN fee_cr IS NOT NULL AND fee_cr < amount
                      AND d NOT ILIKE '%СБП%' THEN fee_cr ELSE 0 END
              + COALESCE(debt_cr, 0)
        END                                                AS gross_dt,

        CASE
            WHEN vat_raw_is_pct THEN NULL
            WHEN vat_raw > 0 AND vat_raw < amount THEN vat_raw
        END AS vat_amount_tmp
    FROM vat
);
