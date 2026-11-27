# Контракт турнирного сервиса

Flask 3 создаётся фабрикой приложения. Blueprints отвечают за публичные страницы, аккаунты, команды, турнирные операции, сетку, кабинеты, переписку и администрирование. Jinja рендерит страницы, формы работают с обычными GET и POST. PyMySQL возвращает словари через `app.db.one` и `app.db.all`. `execute` возвращает `lastrowid`. `transaction()` объединяет все запросы операции на одном соединении. Значения передаются через `%s`.

## Модель

| Таблица | Значимые поля и связи |
|---|---|
| users | id, nickname, email, password_hash, role (player/organizer/admin), created_at, updated_at |
| games | id, name, team_size_min, team_size_max, format (team/solo), created_at |
| teams | id, name, captain_id → users, game_id → games, logo, created_at |
| team_members | id, team_id, user_id, role (captain/player), joined_at. У пользователя один состав. Создание команды и запись капитана атомарны. |
| tournaments | id, title, description, game_id, organizer_id, format (single_elimination/round_robin), registration_type (team/solo), status, max_participants, start_date, end_date, banner, results_snapshot (JSON), completed_at, created_at, updated_at |
| registrations | id, tournament_id, team_id или user_id, status (pending/approved/rejected/withdrawn), rejection_reason, created_at, updated_at |
| matches | id, tournament_id, round_number, match_number, registration1_id, registration2_id, winner_registration_id, score1, score2, status (scheduled/played/walkover/cancelled), next_match_id, scheduled_at, created_at |
| chat_threads | id, tournament_id, participant_user_id, created_at. Уникальная пара турнира и представителя. |
| chat_messages | id, thread_id, sender_id, text, created_at |

Идентичность сторон матча определяется через FK на заявку по уточнению из практикума, с. 100–101. Для представления достаточно JOIN с `teams.name` и `users.nickname`. Круговая таблица сортируется по победам, равным результатам присваивается одинаковое место (1, 1, 3). Все команды изменения сетки, одобрения и заявок блокируют строку турнира до завершения транзакции.

## HTTP и права

Все идентификаторы в угловых скобках обозначают положительные ID соответствующей сущности. Для авторизованных маршрутов гость перенаправляется на вход. Для запроса с неверной ролью сервер возвращает 403. Загруженные изображения и статические ресурсы публичны.

| Метод | URL | Кто имеет доступ и что получает |
|---|---|---|
| GET | `/` | Все. Каталог, параметры q, game, sort, page. |
| GET | `/history` | Все. Завершённые турниры, сортировка по completed_at, параметры game, page. |
| GET | `/tournaments/<id>` | Все для published/in_progress/completed. Для draft/cancelled только организатор-владелец. |
| GET | `/uploads/<filename>`, `/static/<path>` | Все. Файл изображения или ресурс приложения. |
| GET, POST | `/register`, `/login` | Гость. Уже вошедший пользователь перенаправляется на главную. |
| POST | `/logout` | Клиент с действительным CSRF-токеном. Сессия очищается. |
| GET, POST | `/profile` | Игрок, собственная учётная запись. |
| GET | `/team` | Игрок. Его команда и состав или пустое состояние. |
| GET, POST | `/teams/new` | Игрок без команды. Создание команды и назначение своего капитанства. |
| POST | `/teams/<id>/members` | Капитан этой команды. Добавление по nickname. |
| POST | `/teams/<id>/members/<user_id>/remove` | Капитан этой команды. Исключение игрока. |
| GET | `/my/tournaments`, `/my/matches` | Игрок. Своё и командное участие. |
| GET | `/organizer/tournaments` | Организатор. Его турниры всех статусов. |
| GET, POST | `/tournaments/new` | Организатор. Создание черновика. |
| GET, POST | `/tournaments/<id>/edit` | Организатор своего черновика. |
| POST | `/tournaments/<id>/publish`, `/tournaments/<id>/unpublish` | Организатор своего турнира. Публикация заполненного черновика с баннером, снятие публикации до первой заявки. |
| POST | `/tournaments/<id>/delete` | Организатор своего черновика. Загруженный баннер удаляется после подтверждения транзакции. |
| POST | `/tournaments/<id>/cancel` | Организатор своего черновика, опубликованного или действующего турнира. Запланированные матчи получают статус cancelled, сыгранные результаты сохраняются. |
| POST | `/tournaments/<id>/apply` | Игрок без команды по этой дисциплине для solo, капитан команды нужной дисциплины для team. Набор открыт и есть места по числу одобренных заявок. |
| POST | `/registrations/<id>/withdraw` | Владелец одиночной заявки или капитан команды. Заявка pending, турнир published. |
| GET | `/tournaments/<id>/registrations` | Организатор своего турнира. Очередь и решения. |
| POST | `/registrations/<id>/approve`, `/registrations/<id>/reject` | Организатор турнира заявки. Модерация pending при открытой регистрации. |
| POST | `/tournaments/<id>/generate-bracket` | Организатор своего турнира. По одобренным заявкам. Даты раундов распределяются внутри интервала турнира, единственный раунд назначается на дату начала. |
| POST | `/matches/<id>/result` | Организатор турнира этого матча. Режим played, поля score1/score2, либо режим walkover с winner_registration_id. |
| POST | `/tournaments/<id>/finish` | Организатор своего турнира. Все матчи имеют конечный результат. |
| GET | `/tournaments/<id>/chats` | Организатор своего турнира. Список каналов. |
| GET, POST | `/chats/<id>` | Организатор-владелец или участник, которому принадлежит канал. Полная переписка и отправка поля text. |
| GET | `/chats/<id>/messages?after=<message_id>` | Те же участники канала. JSON messages, возрастающие id после указанного. |
| GET, POST | `/admin/users`, `/admin/games` | Администратор. GET открывает список, POST создаёт запись. |
| POST | `/admin/users/<id>`, `/admin/games/<id>` | Администратор. Изменение записи. |
| POST | `/admin/users/<id>/delete`, `/admin/games/<id>/delete` | Администратор. Удаление с проверкой связанных объектов. |

Любая POST-форма содержит csrf_token. Отсутствующий или чужой токен возвращает 403 до обращения к предметной операции. Формы аккаунта используют nickname, email, password, confirm. Поля формы турнира соответствуют колонкам модели. Даты передаются в ISO-формате. После успешной записи сервер перенаправляет клиента. Капитан пишет от своего user_id. Чат обновляется каждые 8 секунд при открытой вкладке.

## Снимок результатов

Поле `tournaments.results_snapshot` сохраняется вместе со статусом completed и временем completed_at в транзакции завершения турнира. В объекте JSON два массива.

| Ключ | Значение |
|---|---|
| rounds | Массив раундов в игровом порядке, каждый содержит матчи по match_number. Запись хранит id, tournament_id, round_number, match_number, registration1_id, registration2_id, winner_registration_id, score1, score2, status, next_match_id, scheduled_at, created_at, name1, name2. Пустая сторона или неопределённый счёт представлены null. |
| standings | Итоговые строки в порядке мест. Каждая хранит registration_id, name, rank, wins, draws, losses, played. Одинаковые позиции в круговой таблице соответствуют одинаковому числу побед. |

Карточка завершённого турнира берёт имена, пары, результаты, места и число участников из снимка. Последующая смена ника или допустимое удаление учётной записи сохраняют историю турнира. При отсутствии снимка применяется расчёт из существующих записей.
