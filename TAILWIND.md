# Tailwind для админки

Стили админки: `static/css/input.css` (исходник) → `static/css/output.css` (собранный, лежит в git).
Django читает только `output.css`, поэтому **просто запустить проект можно без Node**.
Node нужен только тому, кто меняет стили.

## Установка (один раз)

1. Поставить Node.js LTS: https://nodejs.org (или `brew install node` на Mac).
2. В папке проекта:

```bash
npm install
```

Ставит Tailwind ровно тех версий, что в `package-lock.json`. Папка `node_modules/` в git не идёт.

## Работа

```bash
npm run css:watch    # пока правишь — пересобирает output.css на каждое сохранение
npm run css:build    # перед коммитом — минифицированная сборка
```

## Правила проекта

- Свои стили — классами `pk-*` в `input.css` (внутри `@layer components`), а не Tailwind-утилитами
  в Python-коде админок.
- Утилиты Tailwind (`text-[12px]`, `mb-3` …) можно писать в шаблонах `templates/` — сборка их найдёт.
- После правки `input.css` обязательно пересобрать и закоммитить `output.css` — иначе на сервере
  и у коллег стили не появятся.
