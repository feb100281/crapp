-- =============
-- Парсим заказы в паркет
-- =============

COPY (
    SELECT 
        CURRENT_DATE AS date_uploaded,
        $xls_file::text as source,
        "GUID_ЗК"::TEXT AS guid_order,
        "GUID_Номенклатуры"::TEXT AS guid_number,
        "GUID_Характеристики"::TEXT AS guid_character,
        "Артикул"::TEXT AS article,

        TRY_STRPTIME(
            "Дата и время изменения",
            '%d.%m.%Y %H:%M:%S'
        ) AS date_change,

        TRY_CAST("Итоговая сумма" AS DECIMAL(12,2)) AS amount_final,
        TRY_CAST("Итоговая цена" AS DECIMAL(12,2)) AS price_final,

        "Клиент"::TEXT AS client_name,

        TRY_CAST("Кол." AS DECIMAL(12,2)) AS qty,

        "Менеджер"::TEXT AS manager,
        "Номер Заказа"::TEXT AS order_number,
        "Номер на сайте"::TEXT AS number_on_site,
        "Подразделение"::TEXT AS department,
        "ПричинаОтмены"::TEXT AS cancel_reason,
        "РабочееНаименование"::TEXT AS working_name,
        "Склад"::TEXT AS warehouse_name,
        "Статус"::TEXT AS order_status,

        TRY_CAST("СуммаАвтоСкидки" AS DECIMAL(12,2)) AS amount_discount_auto,
        TRY_CAST("СуммаАгентскойСкидки" AS DECIMAL(12,2)) AS amount_discount_agent,
        TRY_CAST("СуммаРучнойСкидки" AS DECIMAL(12,2)) AS amount_discount_manual,

        "Тип операции"::TEXT AS oper_type,

        TRY_CAST("Цена полная" AS DECIMAL(12,2)) AS price_full,

        "Штрихкод"::TEXT AS barcode,

        TRY_CAST("% Авто скидки" AS DECIMAL(12,2)) AS prc_discount_auto,
        TRY_CAST("% АгентскойСкидки" AS DECIMAL(12,2)) AS prc_discount_agent,
        TRY_CAST("% РучнойСкидки" AS DECIMAL(12,2)) AS prc_discount_manual

    FROM read_xlsx(
        $xls,
        range = $xls_range,
        header = true
    )

    WHERE "GUID_ЗК" IS NOT NULL
)
TO $parquet_file
(
    FORMAT PARQUET,
    COMPRESSION ZSTD
);