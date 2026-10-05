

    
WITH source AS (

    -- ============================================================
    -- 1. Собираем всех контрагентов из банковских выписок:
    --    и плательщиков, и получателей.
    -- ============================================================

    SELECT DISTINCT
        inn_payer AS inn,
        payer_name AS name_original,
        payer_account AS ba

    FROM read_parquet(
        $bs
    )

    UNION

    SELECT DISTINCT
        inn_receiver AS inn,
        receiver_name AS name_original,
        receiver_account AS ba

    FROM read_parquet(
        $bs
    )
),

filt AS (

    -- ============================================================
    -- 2. Убираем строки без ИНН.
    -- ============================================================

    SELECT DISTINCT
        *
    FROM source

    WHERE inn IS NOT NULL
      AND trim(inn::VARCHAR) <> ''
),

bas AS (

    -- ============================================================
    -- 3. На один ИНН собираем:
    --    - одно оригинальное название из банковской выписки;
    --    - список всех известных банковских счетов.
    --
    -- name_original нужен как fallback, если DaData ничего не нашла.
    -- ba сохраняем как JSON-массив для SQLite / Django JSONField.
    -- ============================================================

    SELECT
        inn,

        list(
            DISTINCT name_original
            ORDER BY name_original DESC
        )[1] AS name_original,

        to_json(
            list(
                DISTINCT ba
                ORDER BY ba
            )
        ) AS ba

    FROM filt

    GROUP BY inn
),

change_name AS (

    -- ============================================================
    -- 4. Соединяем банковские данные с результатами DaData.
    --
    -- Если DaData вернула название — используем его.
    -- Если нет — оставляем исходное название из выписки.
    -- ============================================================

    SELECT
        p.*,

        COALESCE(
            p.payload->'suggestions'->0->>'value',
            b.name_original
        ) AS name,

        b.ba

    FROM read_parquet(
        $cp
    ) p

    LEFT JOIN bas b
        ON b.inn = p.inn
),

unpacked AS (

    -- ============================================================
    -- 5. Распаковываем нужные поля из raw JSON DaData.
    --
    -- Полный payload при этом сохраняем.
    -- ============================================================

    SELECT
        inn,

        -- ОГРН / ОГРНИП
        payload->'suggestions'->0->'data'->>'ogrn'
            AS ogrn,

        -- --------------------------------------------------------
        -- Нормализуем название:
        -- uppercase + убираем кавычки.
        -- --------------------------------------------------------

        UPPER(
            TRIM(
                regexp_replace(
                    name,
                    '["«»\\]',
                    '',
                    'g'
                )
            )
        ) AS name,

        -- Юридический адрес
        payload->'suggestions'->0->'data'->'address'->>'value'
            AS address,

        -- --------------------------------------------------------
        -- Дата регистрации.
        --
        -- DaData возвращает Unix timestamp в миллисекундах,
        -- поэтому делим на 1000 и приводим к DATE.
        -- --------------------------------------------------------

        to_timestamp(
            TRY_CAST(
                payload->'suggestions'->0->'data'->'state'->>'registration_date'
                AS BIGINT
            ) / 1000
        )::DATE AS registration_date,

        -- Руководитель
        payload->'suggestions'->0->'data'->'management'->>'name'
            AS manager_name,

        -- Страна
        payload->'suggestions'->0->'data'->'address'->'data'->>'country'
            AS country,

        -- Регион
        payload->'suggestions'->0->'data'->'address'->'data'->>'region'
            AS region,

        -- Статус компании:
        -- ACTIVE / LIQUIDATED / BANKRUPT / ...
        payload->'suggestions'->0->'data'->'state'->>'status'
            AS status,

        -- Когда snapshot был получен от DaData
        fetched_at AS updated_at,

        -- Список банковских счетов
        ba,

        -- Полный raw payload DaData
        payload

    FROM change_name
),

splits AS (

    -- ============================================================
    -- 6. Разбиваем название на слова.
    --
    -- Нужно для определения ОПФ в начале строки:
    --
    -- ООО ДРИМ РИЭЛТИ
    -- ↓
    -- ['ООО', 'ДРИМ', 'РИЭЛТИ']
    -- ============================================================

    SELECT
        *,

        string_split(name, ' ') AS splited,

        string_split(name, ' ')[1] AS first_val

    FROM unpacked
)

-- ================================================================
-- 7. Финальный набор данных для Counterparty.
--
-- Если название начинается с ОПФ:
--
-- ООО ДРИМ РИЭЛТИ
--      ↓
-- ДРИМ РИЭЛТИ ООО
--
-- Если ОПФ в начале нет — название оставляем как есть.
-- ================================================================

SELECT
    inn,
    ogrn,

    CASE
        WHEN first_val IN (
            'ООО',
            'АО',
            'ЗАО',
            'ОАО',
            'ПАО',
            'ИП'
        )
        THEN
            TRIM(
                array_to_string(
                    splited[2:],
                    ' '
                )
                || ' '
                || first_val
            )

        ELSE name
    END AS name,

    address,
    registration_date,
    manager_name,
    country,
    region,
    COALESCE(status,'NA') as status,
    updated_at,
    ba,
    payload

FROM splits

ORDER BY name;