# Касса (черновой анализ)

1. `python manage.py cash_desk <job_id>` (или «Касса (карточки 50)» в Командах) → `data/parquet/cash/`.
2. `cash_desk_flow.sql` — касса в формате банковского ДДС (`cash_desk_flow`), сдача в банк — из выписок.
3. `cash_desk_check.sql` — наш остаток против сальдо 1С по дням и месяцам («деньги в пути»).

Прогнать руками (из папки проекта, в venv):

```python
import duckdb
con = duckdb.connect()
con.execute("ATTACH 'db.sqlite3' AS target_db (TYPE SQLITE)")
for f in ("sql/cash/cash_desk_flow.sql", "sql/cash/cash_desk_check.sql"):
    con.execute(open(f).read().replace("__CASH_DIR__", "data/parquet/cash"))
print(con.sql("SELECT * FROM cash_desk_month"))
print(con.sql("SELECT * FROM cash_desk_daily WHERE abs(in_transit) >= 0.01"))
```
