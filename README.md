# Студентський розклад із записами

Неофіційний студентський сервіс на основі відкритого коду
[`kpi-ua/schedule.kpi.ua`](https://github.com/kpi-ua/schedule.kpi.ua). Проєкт не пов’язаний з адміністрацією КПІ
й не представляє університет; у разі розбіжностей перевіряйте розклад на
[`schedule.kpi.ua`](https://schedule.kpi.ua).

## Можливості

- календарна навігація за тижнями;
- незмінні архівні знімки розкладу для вже прикріплених матеріалів;
- записи пар до 500 МіБ та вкладення до 100 МіБ;
- розподілена українська транскрибація: запрошені Windows-клієнти запускають Whisper large-v3 на власних GPU;
- сегментні й послівні таймкоди, пошук по тексту та перехід до потрібного моменту;
- копіювання посилання, завантаження оригінального медіа та транскрипції у TXT;
- окрема сторінка адміністратора для видалення записів.

Записи, транскрипції та вкладення доступні всім, хто відкриє матеріали відповідної пари. Завантажуйте лише
те, що маєте право поширювати й передавати учасникам транскрибації. Для розпізнавання сервер тимчасово віддає
одному запрошеному community-клієнту лише нормалізовану аудіодоріжку без відео й метаданих. Власник такого
комп’ютера технічно може зберегти звук, тому в першій версії токени слід видавати лише довіреним людям.

## Архітектура

- `src/` — React 18, TypeScript, Vite і Tailwind CSS;
- `transcription_service/` — CPU-only FastAPI-сервер, SQLite-черга, токени й оренди завдань;
- `community-transcriber-windows/` — окремий Windows tray-клієнт із faster-whisper і планувальником;
- `nginx.conf` — SPA та same-origin проксі `/recordings-api`;
- `docker-compose.yml` — рекомендований спосіб запустити весь стек.

## Швидкий запуск

Для вебсервісу потрібен лише Docker. NVIDIA GPU на сервері більше не вимагається.

```bash
docker compose up --build
```

Після запуску відкрийте [http://127.0.0.1:3000](http://127.0.0.1:3000). Записи й база зберігаються в іменованому
Docker volume `student-schedule_transcription-data`, тому переживають перевстановлення контейнера. Без
підключеного Windows-клієнта записи лишатимуться в черзі — це очікувана поведінка.

```bash
docker compose logs -f transcriber
docker compose down
```

Скопіюйте `.env.example` у `.env`, щоб увімкнути адміністратора й community-токени або змінити ліміти та адресу.
Реальний `.env`, медіафайли, база, кеші моделей і результати збірки виключені з Git та Docker build context.
Окремий pepper для worker-токенів можна безпечно створити без виводу секрету в консоль:

```bash
python transcription_service/scripts/ensure_worker_token_pepper.py
```

Основні змінні:

| Змінна                                         | Типове значення | Призначення                              |
| ---------------------------------------------- | --------------- | ---------------------------------------- |
| `TRANSCRIPTION_LOCAL_WORKER_ENABLED`           | `false`         | опційний старий локальний Whisper-воркер |
| `TRANSCRIPTION_COMMUNITY_LEASE_SECONDS`        | `600`           | строк оренди завдання між heartbeat      |
| `TRANSCRIPTION_COMMUNITY_MAX_ATTEMPTS`         | `5`             | ліміт невдалих обчислень                 |
| `TRANSCRIPTION_MAX_WORKER_RESULT_BYTES`        | `8388608`       | ліміт JSON-транскрипції від клієнта      |
| `TRANSCRIPTION_AUDIO_PREPARATION_ENABLED`      | `true`          | фонове створення Ogg/Opus для воркерів   |
| `TRANSCRIPTION_AUDIO_PREPARATION_MAX_ATTEMPTS` | `3`             | ліміт спроб нормалізації аудіо           |
| `TRANSCRIPTION_MAX_UPLOAD_BYTES`               | `524288000`     | ліміт запису                             |
| `TRANSCRIPTION_MAX_ATTACHMENT_BYTES`           | `104857600`     | ліміт вкладення                          |
| `APP_BIND_ADDRESS`                             | `127.0.0.1`     | адреса публікації вебконтейнера          |
| `APP_PORT`                                     | `3000`          | порт вебконтейнера                       |

Повний перелік backend-налаштувань та API наведено в
[`transcription_service/README.md`](transcription_service/README.md).

## Адміністратор

Адміністратор вимкнений, доки не задано всі секрети:

```dotenv
VITE_ADMIN_PATH=/private-admin-path
TRANSCRIPTION_ADMIN_USERNAME=your-admin-name
TRANSCRIPTION_ADMIN_PASSWORD_HASH='pbkdf2_sha256$600000$...$...'
TRANSCRIPTION_ADMIN_SESSION_SECRET=your-long-random-token
TRANSCRIPTION_WORKER_TOKEN_PEPPER=another-independent-random-secret
```

Згенерувати PBKDF2-хеш і незалежний секрет сесії можна всередині backend-контейнера:

```bash
docker compose run --rm transcriber python3 -c "from getpass import getpass; from app.auth import hash_password; print(hash_password(getpass('Password: ')))"
docker compose run --rm transcriber python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Прихований шлях не є захистом сам по собі: backend окремо перевіряє логін і пароль та використовує підписану
`HttpOnly`-сесію. Попередня назва `VITE_LOCAL_ADMIN_PATH` ще підтримується для сумісності, але нові конфігурації
мають використовувати `VITE_ADMIN_PATH`.

Після входу адміністратор бачить блок «Учасники транскрибації». Створіть у ньому окремий токен для кожного
комп’ютера й передайте токен власнику безпечним каналом. Відкритий токен показується лише один раз; у базі
зберігається HMAC, а доступ можна відкликати з тієї ж сторінки. Дефолтних токенів немає.

## Windows-клієнт спільноти

Самодостатній підпроєкт міститься в
[`community-transcriber-windows/`](community-transcriber-windows/README.md). Він працює у системному треї,
зберігає токен у Windows Credential Manager, завантажує Whisper large-v3 до першого завдання та підтримує три
режими часу:

- цілодобово;
- ніколи (пауза);
- довільний набір щоденних проміжків, зокрема `10:00–16:00` і перехід через північ `22:00–06:00`.

Клієнт сам робить вихідні HTTPS-запити, тому вхідний порт на домашньому ПК не потрібен. Сервер атомарно видає
один запис в оренду, клієнт підтримує її heartbeat-запитами й повертає обмежений JSON із послівними таймкодами.
Перед видачею сервер у фоні створює mono 16 кГц Ogg/Opus приблизно 32 кбіт/с, а клієнт перевіряє точний розмір
і SHA-256 до запуску Whisper. Оригінал лишається окремо для вебплеєра та завантаження.
Якщо ПК заснув, зник із мережі або токен відкликали, прострочене завдання повертається в чергу. Повторне
надсилання того самого результату є ідемпотентним.

Старий локальний `FasterWhisperTranscriber` не видалено. Для приватної інсталяції його можна ввімкнути окремо,
встановивши `transcription_service/requirements-local-worker.txt` і задавши
`TRANSCRIPTION_LOCAL_WORKER_ENABLED=true`; стандартний CPU Docker-образ його навмисно не містить.

## Розробка та перевірки

Frontend:

```bash
npm ci
npm run dev
npm run prettier
npm run lint
npm run build
```

Backend-тести не завантажують Whisper і не потребують GPU:

```bash
cd transcription_service
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
```

Для Linux/macOS використовуйте відповідні `.venv/bin/...` команди. CI виконує форматування, lint,
production-збірку, перевірку Compose та всі backend-тести.

Core-тести Windows-клієнта також не завантажують модель:

```powershell
cd community-transcriber-windows
py -m pip install -e ".[dev]"
py -m pytest
```

## Перед публічним розгортанням

Compose за замовчуванням слухає лише `127.0.0.1` і призначений для локального або захищеного використання.
Перед відкриттям сервісу в інтернеті налаштуйте зовнішній HTTPS reverse proxy, домен у
`TRANSCRIPTION_ALLOWED_HOSTS`, точний origin у `TRANSCRIPTION_ALLOWED_ORIGINS` та
`TRANSCRIPTION_SECURE_COOKIES=true`. Для відкритих завантажень також потрібні автентифікація або квоти,
rate limiting, моніторинг диска, резервні копії та політика видалення даних. Заголовок
`X-KPI-Local-Request` захищає від небажаних browser-origin запитів, але не замінює авторизацію.
Захист має охоплювати й запис архівних знімків розкладу: вони навмисно незмінні після створення, тому
публічний `PUT /schedule-snapshots/...` без довіреної авторизації не можна відкривати в інтернет.

Community API передає bearer-токени й записи, тому для віддалених клієнтів звичайний HTTP заборонений: потрібен
валідний HTTPS-сертифікат, а reverse proxy не повинен логувати `Authorization` або тіла відповідей. Токен
ідентифікує запрошений пристрій, але не може криптографічно довести, що власник справді запустив large-v3 або не
скопіював отриманий запис. Для повністю відкритої мережі знадобляться репутація, контрольні завдання чи повторна
перевірка результатів; поточна версія розрахована на довірених запрошених учасників.

## Ліцензія та походження

Код поширюється за умовами [Mozilla Public License 2.0](LICENSE). Частини frontend походять з
[`kpi-ua/schedule.kpi.ua`](https://github.com/kpi-ua/schedule.kpi.ua); зміни цього форку описані в історії Git.
