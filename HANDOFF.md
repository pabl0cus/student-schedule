# Handoff: Student Schedule

Стан передавання: **2026-09-04**. Цей каталог є авторитетним знімком усіх локальних доробок до
`kpi-ua/schedule.kpi.ua`. Не замінюйте його чистим upstream checkout: тут живе самостійна історія проєкту.
Локальні секрети та користувацькі дані навмисно не входять до Git.

Upstream base: commit `2c20d3d559cdb412b925528feb4633491bd1ccbe`, branch `master`. Основний репозиторій:
`https://github.com/pabl0cus/student-schedule`.

## Що вже реалізовано

- календарний вибір довільного тижня та незмінні архівні знімки розкладу;
- прикріплення записів пар до 500 МіБ і файлів до 100 МіБ;
- плеєр із пошуком по транскрипції, переходом за таймкодом, копіюванням посилання та завантаженням медіа/TXT;
- захищена адмін-сесія для видалення записів і керування токенами community-воркерів;
- CPU-only FastAPI/SQLite сервер і фонове створення mono 16 кГц Ogg/Opus без відео та метаданих;
- окремий Windows tray-клієнт, який запускає `faster-whisper`/Whisper large-v3 на GPU учасника;
- оренди завдань, heartbeat, retry/release, контроль SHA-256 і точного розміру аудіо;
- режими клієнта «цілодобово», «ніколи» та кілька часових вікон, включно з переходом через північ.

## Структура

- `src/` — React 18, TypeScript, Vite, Tailwind CSS;
- `transcription_service/` — FastAPI, SQLite, медіа, архівні тижні та community API;
- [student-schedule-worker-windows](https://github.com/pabl0cus/student-schedule-worker-windows) — окремий
  репозиторій Windows-клієнта;
- `docker-compose.yml`, `Dockerfile`, `nginx.conf` — CPU-only серверний стек;
- `README.md` — запуск, архітектура й межі безпеки;
- `AGENTS.md` — обов'язкові правила для наступного coding agent;
- README кожного підпроєкту — його API, локальний запуск і пакування.

## Перший запуск на сервері

1. Прочитати `AGENTS.md`, кореневий `README.md` і `transcription_service/README.md`.
2. Скопіювати `.env.example` у локальний `.env`. Не переносити секрети з попереднього комп'ютера.
3. Згенерувати нові незалежні значення для admin session secret, worker-token pepper і пароля адміністратора.
4. Для публічного сервісу поставити зовнішній HTTPS reverse proxy, задати точні allowed hosts/origins і
   `TRANSCRIPTION_SECURE_COOKIES=true`.
5. Перевірити конфігурацію та запустити стек:

   ```bash
   docker compose config --quiet
   docker compose up -d --build
   docker compose ps
   ```

Типово nginx доступний лише на `127.0.0.1:3000`, а постійні дані зберігаються у named volume
`student-schedule_transcription-data`. GPU на сервері не потрібен. Без підключеного Windows-клієнта записи
очікують у черзі — це нормальний стан.

## Обов'язкові перевірки після змін

```bash
npm ci
npm run prettier
npm run lint
npm run build
docker compose config --quiet

cd transcription_service
python -m pip install -r requirements-dev.txt
python -m pytest
```

Перевірки та пакування Windows-клієнта запускаються в його окремому репозиторії.

Остання локальна перевірка перед передаванням:

- frontend format/lint/production build — успішно;
- backend — **125 tests passed**;
- Windows-клієнт — **74 tests passed**;
- Compose config і реальний Docker smoke test — успішно;
- Windows frozen self-test та Inno Setup build — успішно;
- Whisper-модель під час тестів і збірки не завантажувалась.

## Дані, яких у пакеті немає

Навмисно виключені `.git`, `.env`, SQLite, записи, вкладення, підготовлене аудіо, worker/admin токени,
`node_modules`, virtualenv, кеші, model weights, логи, coverage і build/dist output. Отже цей каталог безпечний
для передавання як вихідний код, але не є backup локального deployment. Секрети слід створити заново вже на
сервері.

Це порожній bootstrap, а не міграція даних. Зібраний Windows installer не входить до основного репозиторію:
його слід відтворити з інструкцій окремого проєкту клієнта.

## Межі поточної версії

- Compose за замовчуванням не є готовою internet-production конфігурацією.
- Відкриті uploads потребують автентифікації або квот, rate limiting, моніторингу диска, backup і retention.
- Для кількох backend-процесів треба винести координацію worker/preparer/delete locks із процесу/файлу в
  спільний механізм; поточний Compose запускає один uvicorn process.
- Startup-перевірка підготовленого аудіо може лінійно перечитувати наявні артефакти для SHA-256.
- Формат підготовленого аудіо варто версіонувати перед майбутньою зміною codec/profile.
- Публічний Windows installer бажано підписувати code-signing сертифікатом.
- Community-токен і lease захищають доступ, але не доводять чесність вузла; зараз модель довіри — запрошені
  учасники.

Основний проєкт і Windows-клієнт опубліковані в окремих репозиторіях. Секрети та production-дані до них не
входять.
