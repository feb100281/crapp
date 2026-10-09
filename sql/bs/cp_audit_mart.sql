-- ============================================================
-- Витрина «Контрагенты и статьи» (dashboard_cp_audit) — SQLite
--
-- Строка = одна разноска строки выписки (или строка без разноски).
-- Переводы между своими счетами не входят: резолверы контрагентов
-- на них не действуют. Жирная: всё для дашборда лежит в строке,
-- включая резолверы, которым строка принадлежит (для ссылок «настроить»).
--
-- Пересобирается целиком после разноски (resolve / resolve_all),
-- правки разноски руками, переноса статей и пересчёта ДДС.
-- ============================================================

DROP TABLE IF EXISTS dashboard_cp_audit;

CREATE TABLE dashboard_cp_audit AS
SELECT
    row_number() OVER (ORDER BY l.id, a.id)           AS id,
    l.id                                              AS line_id,
    l.op_date                                         AS date,
    CAST(strftime('%Y', l.op_date) AS INTEGER)        AS year,
    l.direction                                       AS direction,
    l.inn_adjust                                      AS inn,
    l.cp_name                                         AS cp_name,
    l.kbk                                             AS kbk,
    l.ba_resolver                                     AS ba_key,
    b.number                                          AS ba_number,
    l.description                                     AS description,
    CAST(COALESCE(a.amount, l.amount) AS REAL)        AS amount,
    CASE WHEN a.id IS NULL THEN 1 ELSE 0 END          AS is_open,
    a.cf_item_id                                      AS cf_item_id,
    i.code                                            AS cf_code,
    i.name                                            AS cf_name,
    COALESCE(pi.code, i.code)                         AS article_code,
    COALESCE(pi.name, i.name)                         AS article_name,
    COALESCE(a.manual, 0)                             AS manual,
    rr.id                                             AS rule_resolver_id,
    rr.kind                                           AS rule_kind,
    rk.id                                             AS kbk_resolver_id,
    rb.id                                             AS ba_resolver_id,
    rc.id                                             AS cp_resolver_id
FROM treasury_bsline l
LEFT JOIN treasury_bslinealloc  a  ON a.line_id = l.id
LEFT JOIN treasury_cfitem       i  ON i.id = a.cf_item_id
LEFT JOIN treasury_cfitem       pi ON pi.id = i.parent_id
LEFT JOIN treasury_resolverrule r  ON r.id = a.resolver_rule_id
LEFT JOIN treasury_resolver     rr ON rr.id = r.resolver_id
LEFT JOIN treasury_resolver     rk ON rk.kind = 'KBK' AND rk.key = l.kbk
                                   AND rk.direction = l.direction
LEFT JOIN treasury_resolver     rb ON l.kbk IS NULL AND rb.kind = 'BA'
                                   AND rb.key = l.ba_resolver AND rb.direction = l.direction
LEFT JOIN treasury_resolver     rc ON rc.kind = 'CP' AND rc.match_name IS NULL
                                   AND rc.key = l.inn_adjust AND rc.direction = l.direction
LEFT JOIN treasury_bankaccount  b  ON b.id = l.ba_account_id
WHERE l.intercompany = 0;

CREATE INDEX dashboard_cp_audit_cp ON dashboard_cp_audit (inn, direction);
CREATE INDEX dashboard_cp_audit_line ON dashboard_cp_audit (line_id);
