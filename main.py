import asyncio
import logging
import re
from datetime import datetime, timedelta, time

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from config import BOT_TOKEN, SPREADSHEET_NAME

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

user_states = {}

ALLOWED_CHAT_ID = -1002286714421
TASKS_THREAD_ID = 40448
COMPLETED_THREAD_ID = 43115

REMINDER_36_COL = 15
REMINDER_12_COL = 16
REMINDER_1H_COL = 17
OVERDUE_NOTICE_COL = 18

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)

spreadsheet = client.open(SPREADSHEET_NAME)
sheet = spreadsheet.worksheet("tasks")
admins_sheet = spreadsheet.worksheet("admins")
users_sheet = spreadsheet.worksheet("Users")


def norm_user(username):
    if not username:
        return ""
    # Telegram username не чувствителен к регистру, поэтому
    # @Paul_Koff и @paul_koff должны считаться одним человеком.
    return str(username).replace("@", "").strip().lower()


def get_admins():
    values = admins_sheet.col_values(1)[1:]
    return [norm_user(x) for x in values if x.strip()]


def is_admin(username):
    return norm_user(username) in get_admins()


def save_user(message: Message):
    username = norm_user(message.from_user.username)
    user_id = str(message.from_user.id)

    if not username:
        return

    values = users_sheet.get_all_values()

    for i, row in enumerate(values):
        if row and norm_user(row[0]) == username:
            users_sheet.update_cell(i + 1, 2, user_id)
            return

    role = "admin" if is_admin(username) else "user"
    users_sheet.append_row([username, user_id, role])


def get_user_id_by_username(username):
    username = norm_user(username)
    values = users_sheet.get_all_values()[1:]

    for row in values:
        if len(row) >= 2 and norm_user(row[0]) == username:
            return row[1]

    return None


def is_private_chat(message: Message):
    return message.chat.type == "private"


def is_allowed_message(message: Message):
    if is_private_chat(message):
        return True

    if message.chat.id != ALLOWED_CHAT_ID:
        return False

    return message.message_thread_id == TASKS_THREAD_ID


def is_allowed_callback(callback: CallbackQuery):
    if not callback.message:
        return True

    if callback.message.chat.type == "private":
        return True

    if callback.message.chat.id != ALLOWED_CHAT_ID:
        return False

    return callback.message.message_thread_id == TASKS_THREAD_ID

def active_status(status):
    return status not in ["✅ Готово", "❌ Отменена"]


def find_task(task_id):
    values = sheet.get_all_values()
    for index, row in enumerate(values):
        if row and row[0] == str(task_id):
            while len(row) < 18:
                row.append("")
            return index + 1, row
    return None, None


def get_active_tasks_for_user(username):
    username = norm_user(username)
    result = []

    for row in sheet.get_all_values()[1:]:
        while len(row) < 18:
            row.append("")

        if norm_user(row[2]) == username and active_status(row[6]):
            result.append(row)

    return result


def get_active_tasks():
    result = []

    for row in sheet.get_all_values()[1:]:
        while len(row) < 18:
            row.append("")

        if active_status(row[6]):
            result.append(row)

    return result


def get_review_tasks():
    return [row for row in get_active_tasks() if row[6] == "⏳ На утверждении"]


def get_reassign_tasks():
    return [row for row in get_active_tasks() if row[6] == "⚠️ Требует переназначения"]


def main_menu(username):
    if is_admin(username):
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📌 Все активные"), KeyboardButton(text="⏳ На утверждении")],
                [KeyboardButton(text="✅ Выполненные"), KeyboardButton(text="📊 Статистика")],
                [KeyboardButton(text="👤 Статистика по людям")],
                [KeyboardButton(text="👥 Переназначение"), KeyboardButton(text="📋 Мои задачи")],
            ],
            resize_keyboard=True,
        )

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои задачи")],
            [KeyboardButton(text="📎 Сдать работу"), KeyboardButton(text="⏰ Перенести срок")],
            [KeyboardButton(text="🆘 Нужна помощь"), KeyboardButton(text="❌ Отказаться")],
        ],
        resize_keyboard=True,
    )


def waiting_keyboard(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Беру", callback_data=f"take_{task_id}"),
        InlineKeyboardButton(text="❌ Не могу", callback_data=f"decline_{task_id}"),
        InlineKeyboardButton(text="✏️ Сдвинуть", callback_data=f"move_{task_id}"),
    ]])


def work_keyboard(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📎 Сдать", callback_data=f"submit_{task_id}"),
            InlineKeyboardButton(text="🆘 Помощь", callback_data=f"help_{task_id}"),
            InlineKeyboardButton(text="⏰ Перенести", callback_data=f"move_{task_id}"),
        ],
        [
            InlineKeyboardButton(text="❌ Отказаться", callback_data=f"refuse_{task_id}"),
        ],
    ])


def review_keyboard(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принято", callback_data=f"accept_{task_id}"),
        InlineKeyboardButton(text="✏️ На правки", callback_data=f"rework_{task_id}"),
        InlineKeyboardButton(text="🗑️ Отменить", callback_data=f"cancel_{task_id}"),
    ]])


def task_action_keyboard(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📎 Сдать", callback_data=f"submit_{task_id}"),
            InlineKeyboardButton(text="⏰ Перенести", callback_data=f"move_{task_id}"),
        ],
        [
            InlineKeyboardButton(text="🆘 Помощь", callback_data=f"help_{task_id}"),
            InlineKeyboardButton(text="❌ Отказаться", callback_data=f"refuse_{task_id}"),
        ],
    ])


def move_request_keyboard(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить перенос", callback_data=f"approve_move_{task_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"deny_move_{task_id}"),
    ]])


def reassign_keyboard(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Переназначить", callback_data=f"reassign_{task_id}"),
        InlineKeyboardButton(text="🗑️ Отменить", callback_data=f"cancel_{task_id}"),
    ]])


def admin_help_keyboard(task_id, assignee):
    assignee_username = norm_user(assignee)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤝 Помочь",
                    callback_data=f"admin_help_{task_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Связаться в чате",
                    url=f"https://t.me/{assignee_username}",
                )
            ],
        ]
    )


def make_task_text(task_id, row):
    text = (
        f"📋 ЗАДАЧА #{task_id}\n\n"
        f"Создал: @{row[1]}\n"
        f"Исполнитель: {row[2]}\n"
        f"Дедлайн: {row[4]}\n"
        f"Описание: {row[3]}\n"
        f"Материал: {row[5]}\n"
        f"Статус: {row[6]}"
    )

    if row[7]:
        text += f"\nРезультат: {row[7]}"

    if row[8]:
        text += f"\nКомментарий: {row[8]}"

    text += "\n\n🤖 Управление задачей: @glavzadacha_bot"

    return text


async def update_card(task_id, row, keyboard=None):
    chat_id = row[12]
    message_id = row[13]

    if chat_id and message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=make_task_text(task_id, row),
                reply_markup=keyboard,
            )
        except Exception:
            pass


async def notify_creator(row, text, keyboard=None):
    creator_username = norm_user(row[1])
    creator_user_id = get_user_id_by_username(creator_username)

    if creator_user_id:
        try:
            await bot.send_message(
                int(creator_user_id),
                text,
                reply_markup=keyboard,
            )
            return
        except Exception as e:
            logging.warning(f"Не удалось отправить ЛС @{creator_username}: {e}")

    logging.warning(f"Не найден user_id для @{creator_username}")


DATE_RE = re.compile(r"\b(\d{1,2})\.(0?[1-9]|1[0-2])(?:\.(\d{4}))?\b")

# Время теперь ищем не одним строгим regex, а несколькими вариантами:
# 17:00, 17.00, 17-00, 17 00, 17ч00, 17ч, 17 часов,
# до 17, к 17, в 17, 1700, 5 вечера, полдень, полночь.
TIME_WITH_MINUTES_RE = re.compile(
    r"(?<!\d)([01]?\d|2[0-4])\s*(?::|\.|-|–|—|ч\.?|час(?:а|ов)?|\s+)\s*([0-5]\d)(?!\d)",
    re.IGNORECASE,
)
COMPACT_TIME_RE = re.compile(r"(?<!\d)([01]\d|2[0-3])([0-5]\d)(?!\d)")
HOUR_WITH_CONTEXT_RE = re.compile(
    r"\b(?:до|к|в|на|около|примерно)\s+([01]?\d|2[0-4])(?:\s*(?:ч\.?|час(?:а|ов)?)?)\b",
    re.IGNORECASE,
)
HOUR_WITH_UNIT_RE = re.compile(
    r"(?<!\d)([01]?\d|2[0-4])\s*(?:ч\.?|час(?:а|ов)?)\b",
    re.IGNORECASE,
)
RUSSIAN_DAYTIME_RE = re.compile(
    r"(?<!\d)(1[0-2]|0?[1-9])\s*(утра|дня|вечера|ночи)\b",
    re.IGNORECASE,
)


def _spans_overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _normalize_time(hour, minute=0):
    hour = int(hour)
    minute = int(minute)

    # Пользователи часто пишут «до 24:00» как «до конца дня».
    # datetime.time не принимает 24:00, поэтому считаем это 23:59.
    if hour == 24 and minute == 0:
        return 23, 59

    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute

    return None


def _make_time_candidate(hour, minute, span, score):
    normalized = _normalize_time(hour, minute)
    if not normalized:
        return None

    return {
        "hour": normalized[0],
        "minute": normalized[1],
        "span": span,
        "score": score,
    }


def _candidate_is_ignored(candidate, ignored_spans):
    return any(_spans_overlap(candidate["span"], span) for span in ignored_spans)


def _time_context_score(text, span):
    """Даёт бонус времени, рядом с которым есть слова дедлайна."""
    start, end = span
    before = text[max(0, start - 15):start].lower()
    after = text[end:min(len(text), end + 15)].lower()

    score = 0
    if re.search(r"(?:до|к|в|на|дедлайн|срок)\s*$", before):
        score += 40
    if re.search(r"^\s*(?:сегодня|завтра|дедлайн|срок)", after):
        score += 25

    return score


def find_time_candidates(text, ignored_spans=None):
    """Возвращает варианты времени, не путая их с датами вроде 09.06."""
    ignored_spans = ignored_spans or []
    candidates = []

    def add(candidate):
        if not candidate:
            return
        if _candidate_is_ignored(candidate, ignored_spans):
            return
        candidate["score"] += _time_context_score(text, candidate["span"])
        candidates.append(candidate)

    lower_text = text.lower()

    for word, hour, minute in [
        ("полдень", 12, 0),
        ("полдня", 12, 0),
        ("полудня", 12, 0),
        ("полночь", 0, 0),
        ("полночи", 0, 0),
    ]:
        for match in re.finditer(rf"\b{word}\b", lower_text):
            add(_make_time_candidate(hour, minute, match.span(), 115))

    for match in RUSSIAN_DAYTIME_RE.finditer(text):
        hour = int(match.group(1))
        part = match.group(2).lower()

        if part == "вечера":
            if hour < 12:
                hour += 12
        elif part == "дня":
            if 1 <= hour <= 11:
                hour += 12
            if hour == 24:
                hour = 12
        elif part == "ночи":
            if hour == 12:
                hour = 0
        # «утра» оставляем как есть.

        add(_make_time_candidate(hour, 0, match.span(), 110))

    for match in TIME_WITH_MINUTES_RE.finditer(text):
        add(_make_time_candidate(match.group(1), match.group(2), match.span(), 100))

    # Компактное «до 1700» или «к 0930» берём только при явном контексте,
    # чтобы не ловить случайные четырёхзначные числа.
    for match in COMPACT_TIME_RE.finditer(text):
        before = text[max(0, match.start() - 8):match.start()].lower()
        if re.search(r"(?:до|к|в|на)\s*$", before):
            add(_make_time_candidate(match.group(1), match.group(2), match.span(), 95))

    for match in HOUR_WITH_CONTEXT_RE.finditer(text):
        add(_make_time_candidate(match.group(1), 0, match.span(), 80))

    for match in HOUR_WITH_UNIT_RE.finditer(text):
        add(_make_time_candidate(match.group(1), 0, match.span(), 75))

    # Формат «20.06 17» или «20.06 в 17».
    # Сюда передаются ignored_spans дат, поэтому аккуратно смотрим сразу после даты.
    for date_span in ignored_spans:
        after_start = date_span[1]
        after = text[after_start:after_start + 25]
        match = re.match(
            r"\s*(?:до|к|в|на)?\s*([01]?\d|2[0-4])(?:\s*(?::|\.|-|–|—|ч\.?|\s+)\s*([0-5]\d))?\b",
            after,
            re.IGNORECASE,
        )
        if match:
            minute = match.group(2) if match.group(2) is not None else 0
            span = (after_start + match.start(1), after_start + match.end(0))
            add(_make_time_candidate(match.group(1), minute, span, 90))

    # Убираем дубли: например «17 00» может совпасть с «до 17».
    unique = []
    for candidate in sorted(candidates, key=lambda c: c["score"], reverse=True):
        if any(_spans_overlap(candidate["span"], existing["span"]) for existing in unique):
            continue
        unique.append(candidate)

    return sorted(unique, key=lambda c: c["score"], reverse=True)


def find_time_match(text, ignored_spans=None):
    candidates = find_time_candidates(text, ignored_spans)
    return candidates[0] if candidates else None


def format_time_match(match):
    if not match:
        return ""
    return f"{match['hour']:02d}:{match['minute']:02d}"


def extract_deadline_from_text(text):
    """Достаёт дедлайн из свободного текста.

    Поддерживает старые и новые варианты:
    - сегодня / завтра
    - сегодня 18:00 / завтра до 18:00
    - до завтра до 17 00 / до 17 завтра / к 17 завтра
    - 09.06 / 09.06 18:00 / 09.06 18 00 / 09.06 в 18
    - 18:00 / 18.00 / 18-00 / 18 00 / 18ч / 18 часов
    - 1700 при явном контексте: до 1700
    - 5 вечера / полдень / полночь

    Если время не указано, дальше parse_deadline_to_datetime поставит 23:59,
    то есть старое поведение сохраняется.
    """
    if not text:
        return "не указан"

    raw_text = str(text).strip()
    lower_text = raw_text.lower()
    now = datetime.now()

    date_matches = list(DATE_RE.finditer(raw_text))
    time_match = find_time_match(raw_text, [m.span() for m in date_matches])
    time_text = format_time_match(time_match)

    if "сегодня" in lower_text:
        date_text = now.strftime("%d.%m")
    elif "завтра" in lower_text:
        date_text = (now + timedelta(days=1)).strftime("%d.%m")
    elif date_matches:
        date_text = date_matches[0].group(0)
    else:
        date_text = ""

    if date_text and time_text:
        return f"{date_text} {time_text}"
    if date_text:
        return date_text
    if time_text:
        return f"{now.strftime('%d.%m')} {time_text}"

    return "не указан"


def _remove_spans(text, spans):
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    return text


def clean_task_description(text, assignee, link):
    description = text
    description = description.replace(assignee, "")
    if link:
        description = description.replace(link, "")

    date_matches = list(DATE_RE.finditer(description))
    ignored_spans = [m.span() for m in date_matches]
    time_candidates = find_time_candidates(description, ignored_spans)

    spans_to_remove = ignored_spans + [candidate["span"] for candidate in time_candidates]
    description = _remove_spans(description, spans_to_remove)

    description = re.sub(
        r"\b(до|к|в|на|около|примерно|дедлайн|задача|для|сегодня|завтра|срок|час|часа|часов|ч)\b",
        "",
        description,
        flags=re.IGNORECASE,
    )
    description = re.sub(r"\s+", " ", description).strip(" :-—.,")

    return description


def parse_free_task(text):
    assignee_match = re.search(r"@[\w\d_]+", text)
    link_match = re.search(r"https?://\S+", text)

    if not assignee_match:
        return None

    assignee = assignee_match.group(0)
    link = link_match.group(0) if link_match else ""
    deadline = extract_deadline_from_text(text)
    description = clean_task_description(text, assignee, link)

    return assignee, description, deadline, link


def parse_deadline_to_datetime(deadline_text):
    if not deadline_text:
        return None

    text = str(deadline_text).strip().lower()
    now = datetime.now()

    if text in ["не указан", "-", ""]:
        return None

    date_matches = list(DATE_RE.finditer(text))
    time_match = find_time_match(text, [m.span() for m in date_matches])

    deadline_time = time(23, 59)
    if time_match:
        deadline_time = time(time_match["hour"], time_match["minute"])

    if "сегодня" in text:
        return datetime.combine(now.date(), deadline_time)

    if "завтра" in text:
        return datetime.combine((now + timedelta(days=1)).date(), deadline_time)

    if not date_matches:
        if time_match:
            return datetime.combine(now.date(), deadline_time)
        return None

    match = date_matches[0]
    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3)) if match.group(3) else now.year

    try:
        return datetime(year, month, day, deadline_time.hour, deadline_time.minute)
    except ValueError:
        return None


async def notify_user_by_username(username, text, keyboard=None):
    user_id = get_user_id_by_username(username)

    if not user_id:
        logging.warning(f"Не найден user_id для @{norm_user(username)}")
        return False

    try:
        await bot.send_message(int(user_id), text, reply_markup=keyboard)
        return True
    except Exception as e:
        logging.warning(f"Не удалось отправить ЛС @{norm_user(username)}: {e}")
        return False


def get_message_content_text(message: Message):
    """Возвращает короткое текстовое описание сообщения для Google Sheets и уведомлений.
    Поддерживает текст, документы, фото, видео, аудио, голосовые и видеосообщения.
    Сам файл не кладём в таблицу: при необходимости копируем сообщение адресату в Telegram.
    """
    if message.text:
        text = message.text.strip()
        return text if text else None

    caption = message.caption.strip() if message.caption else ""

    if message.document:
        base = f"📎 Файл приложен: {message.document.file_name or 'документ'}"
    elif message.photo:
        base = "📎 Фото приложено"
    elif message.video:
        base = "📎 Видео приложено"
    elif message.audio:
        base = f"📎 Аудио приложено: {message.audio.file_name or 'аудио'}"
    elif message.voice:
        base = "📎 Голосовое сообщение приложено"
    elif message.video_note:
        base = "📎 Видеосообщение приложено"
    elif getattr(message, "animation", None):
        base = "📎 GIF/анимация приложена"
    else:
        return None

    if caption:
        return f"{base}\nКомментарий: {caption}"

    return base


def get_submission_result_text(message: Message):
    """Совместимость со старой логикой сдачи работы."""
    return get_message_content_text(message)


def get_message_task_text(message: Message):
    """Текст задачи может быть обычным сообщением или подписью к файлу/медиа."""
    if message.text:
        return message.text.strip()
    if message.caption:
        return message.caption.strip()
    return ""


def has_copyable_attachment(message: Message):
    """Есть ли в сообщении вложение, которое можно переслать/скопировать адресату."""
    return any([
        message.document,
        message.photo,
        message.video,
        message.audio,
        message.voice,
        message.video_note,
        getattr(message, "animation", None),
    ])


def get_attachment_material_text(message: Message):
    """Короткая подпись материала задачи для Google Sheets и карточки задачи."""
    if message.document:
        return f"📎 Файл приложен: {message.document.file_name or 'документ'}"
    if message.photo:
        return "📎 Фото приложено"
    if message.video:
        return "📎 Видео приложено"
    if message.audio:
        return f"📎 Аудио приложено: {message.audio.file_name or 'аудио'}"
    if message.voice:
        return "📎 Голосовое сообщение приложено"
    if message.video_note:
        return "📎 Видеосообщение приложено"
    if getattr(message, "animation", None):
        return "📎 GIF/анимация приложена"
    return ""


def build_task_material_text(message: Message, link: str):
    """Материал задачи: вложение, ссылка или оба варианта."""
    attachment_text = get_attachment_material_text(message)
    link = link.strip() if link else ""

    if attachment_text and link:
        return f"{attachment_text}\n🔗 Ссылка: {link}"
    if attachment_text:
        return attachment_text
    if link:
        return link
    return ""


async def copy_message_to_username(username, message: Message, header_text):
    """Копирует файл/медиа адресату по username. Для текстовых сообщений копирование не нужно."""
    if not has_copyable_attachment(message):
        return False

    user_id = get_user_id_by_username(username)

    if not user_id:
        logging.warning(f"Не найден user_id для @{norm_user(username)} при копировании сообщения")
        return False

    try:
        await bot.send_message(int(user_id), header_text)
        await bot.copy_message(
            chat_id=int(user_id),
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        return True
    except Exception as e:
        logging.warning(f"Не удалось скопировать сообщение @{norm_user(username)}: {e}")
        return False


async def copy_submission_to_creator(row, message: Message, task_id):
    """Копирует присланный файл/медиа автору задачи.
    Для текста копирование не нужно: текст уже уходит в уведомлении.
    """
    await copy_message_to_username(
        row[1],
        message,
        f"📎 Файл/медиа по задаче #{task_id} от {row[2]}:",
    )


async def notify_status_change(row, task_id, old_status, new_status, actor_username=None):
    """Уведомляет исполнителя, автора задачи и админов о любой смене статуса."""
    if old_status == new_status:
        return

    actor = f"@{norm_user(actor_username)}" if actor_username else "бот"

    text = (
        f"🔔 Изменился статус задачи #{task_id}\n\n"
        f"Было: {old_status}\n"
        f"Стало: {new_status}\n\n"
        f"👤 Автор: @{norm_user(row[1])}\n"
        f"🧑‍💻 Исполнитель: {row[2]}\n"
        f"📝 Описание: {row[3]}\n"
        f"⏰ Дедлайн: {row[4]}\n"
        f"Изменил: {actor}"
    )

    sent_user_ids = set()

    async def send_once(username):
        user_id = get_user_id_by_username(username)
        if not user_id or user_id in sent_user_ids:
            return
        sent_user_ids.add(user_id)
        try:
            await bot.send_message(int(user_id), text)
        except Exception as e:
            logging.warning(f"Не удалось отправить уведомление о статусе @{norm_user(username)}: {e}")

    await send_once(row[2])
    await send_once(row[1])

    for admin_username in get_admins():
        await send_once(admin_username)


async def change_task_status(row_number, row, task_id, new_status, actor_username=None):
    """Единая точка смены статуса: обновляет таблицу и рассылает уведомления."""
    old_status = row[6]
    sheet.update_cell(row_number, 7, new_status)
    await notify_status_change(row, task_id, old_status, new_status, actor_username)

async def create_task_from_parts(message, assignee, description, deadline, link):
    task_id = len(sheet.get_all_values())
    material_text = build_task_material_text(message, link)

    row = [
        task_id,
        norm_user(message.from_user.username),
        assignee,
        description,
        deadline,
        material_text,
        "❓ Ожидает подтверждения",
        "",
        "",
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        "нет",
        "",
        "",
        "",
        "",
        "",
        "",
    ]

    sheet.append_row(row)

    sent = await message.answer(
        make_task_text(task_id, [str(x) for x in row]),
        reply_markup=None,
    )

    row_number, saved_row = find_task(task_id)
    sheet.update_cell(row_number, 13, sent.chat.id)
    sheet.update_cell(row_number, 14, sent.message_id)

    logging.info(f"ASSIGNEE={assignee} FOUND_USER_ID={get_user_id_by_username(assignee)}")
    assignee_user_id = get_user_id_by_username(assignee)

    if assignee_user_id:
        try:
            await bot.send_message(
                int(assignee_user_id),
                f"📋 Тебе поставили задачу #{task_id}\n\n"
                f"Поставил: @{norm_user(message.from_user.username)}\n"
                f"Описание: {description}\n"
                f"Дедлайн: {deadline}\n"
                f"Материал: {material_text if material_text else 'не указан'}",
                reply_markup=waiting_keyboard(task_id),
            )

            await copy_message_to_username(
                assignee,
                message,
                f"📎 Материал к задаче #{task_id} от @{norm_user(message.from_user.username)}:",
            )
        except Exception as e:
            logging.warning(f"Не удалось отправить задачу исполнителю {assignee}: {e}")
    else:
        logging.warning(
            f"Не найден user_id для исполнителя {assignee}. "
            "Исполнитель должен написать боту /start в личке."
        )

    return task_id


@dp.message(Command("start"))
async def start(message: Message):
    if not is_allowed_message(message):
        return

    save_user(message)

    logging.info(
        f"START USER: {message.from_user.username} ID: {message.from_user.id}"
    )

    await message.answer(
        "Кабинет открыт ✅",
        reply_markup=main_menu(message.from_user.username),
    )


@dp.message(Command("задача"))
async def create_task_command(message: Message):
    if not is_allowed_message(message):
        return

    text = re.sub(r"^/задача(@\w+)?", "", message.text).strip()

    try:
        parts = text.split("|")
        left = parts[0].strip()
        deadline = parts[1].strip()
        link = parts[2].strip()

        words = left.split()
        assignee = words[0]
        description = " ".join(words[1:])

        await create_task_from_parts(message, assignee, description, deadline, link)

    except Exception:
        await message.answer(
            "Не понял задачу.\n\n"
            "Формат:\n"
            "/задача @user описание | 09.06 18:00 | ссылка\n\n"
            "Или свободно:\n"
            "@user сделать обложку до завтра до 18:00 https://...\n\n"
            "Можно также прикрепить файл/фото/видео и написать задачу в подписи."
        )


@dp.message(F.text == "📋 Мои задачи")
@dp.message(Command("мои"))
async def my_tasks(message: Message):
    if not is_allowed_message(message):
        return

    tasks = get_active_tasks_for_user(message.from_user.username)

    if not tasks:
        await message.answer("У тебя нет активных задач ✅")
        return

    for row in tasks:
        if row[6] == "❓ Ожидает подтверждения":
            keyboard = waiting_keyboard(row[0])
        elif row[6] in ["🔄 В работе", "✏️ На доработке"]:
            keyboard = task_action_keyboard(row[0])
        else:
            keyboard = None

        await message.answer(
            make_task_text(row[0], row),
            reply_markup=keyboard,
        )


@dp.message(F.text == "📌 Все активные")
async def all_active(message: Message):
    if not is_allowed_message(message):
        return

    if not is_admin(message.from_user.username):
        return

    tasks = get_active_tasks()

    if not tasks:
        await message.answer("Активных задач нет ✅")
        return

    text = "📌 Все активные задачи:\n\n"
    for row in tasks:
        text += f"#{row[0]} — {row[2]} — {row[3]} — {row[4]} — {row[6]}\n"

    await message.answer(text)


@dp.message(F.text == "⏳ На утверждении")
async def review_list(message: Message):
    if not is_allowed_message(message):
        return

    if not is_admin(message.from_user.username):
        return

    tasks = get_review_tasks()

    if not tasks:
        await message.answer("Нет задач на утверждении ✅")
        return

    for row in tasks:
        await message.answer(make_task_text(row[0], row), reply_markup=review_keyboard(row[0]))


@dp.message(F.text == "👥 Переназначение")
async def reassign_list(message: Message):
    if not is_allowed_message(message):
        return

    if not is_admin(message.from_user.username):
        return

    tasks = get_reassign_tasks()

    if not tasks:
        await message.answer("Нет задач на переназначение ✅")
        return

    for row in tasks:
        await message.answer(make_task_text(row[0], row), reply_markup=reassign_keyboard(row[0]))


@dp.message(F.text == "✅ Выполненные")
async def completed_tasks_summary(message: Message):
    if not is_allowed_message(message):
        return

    if not is_admin(message.from_user.username):
        return

    rows = sheet.get_all_values()[1:]
    completed = []

    for row in rows:
        while len(row) < 18:
            row.append("")

        if row[6] == "✅ Готово":
            completed.append(row)

    if not completed:
        await message.answer("✅ ВЫПОЛНЕННЫЕ ЗАДАЧИ\n\nПока нет выполненных задач.")
        return

    text = "✅ ВЫПОЛНЕННЫЕ ЗАДАЧИ\n\n"

    for row in completed[-20:]:
        result = row[7] if row[7] else "не указан"
        finished_at = row[10] if row[10] else "не указано"

        text += (
            f"#{row[0]} — {row[3]}\n\n"
            f"👤 Автор: @{norm_user(row[1])}\n"
            f"🧑‍💻 Исполнитель: {row[2]}\n"
            f"🔗 Результат: {result}\n"
            f"🕒 Выполнено: {finished_at}\n"
            f"──────────────\n\n"
        )

    text += f"📋 Показано: {min(len(completed), 20)} из {len(completed)}"

    await message.answer(text)


@dp.message(F.text == "📊 Статистика")
async def tasks_stats(message: Message):
    if not is_allowed_message(message):
        return

    if not is_admin(message.from_user.username):
        return

    rows = sheet.get_all_values()[1:]

    total = done = overdue = moved = waiting = refused = in_progress = review = rework = 0

    for row in rows:
        while len(row) < 18:
            row.append("")

        if not row[0]:
            continue

        total += 1
        status = row[6]
        comment = row[8].lower() if row[8] else ""

        if status == "✅ Готово":
            done += 1
        if status == "🔴 Просрочена" or row[11].lower() == "да":
            overdue += 1
        if "перенос" in comment or "перенести" in comment or "срок" in comment:
            moved += 1
        if status == "❓ Ожидает подтверждения":
            waiting += 1
        if status == "⚠️ Требует переназначения":
            refused += 1
        if status == "🔄 В работе":
            in_progress += 1
        if status == "⏳ На утверждении":
            review += 1
        if status == "✏️ На доработке":
            rework += 1

    updated_at = datetime.now().strftime("%d.%m.%Y %H:%M")

    text = (
        "📊 СТАТИСТИКА ЗАДАЧ\n\n"
        "Общая статистика по всем задачам\n\n"
        f"📄 Всего выдано: {total}\n"
        f"✅ Выполнено: {done}\n"
        f"🔴 Просрочено: {overdue}\n"
        f"🔄 Перенесено: {moved}\n"
        f"⏳ Ожидают подтверждения: {waiting}\n"
        f"❌ Отказано / переназначено: {refused}\n"
        f"🛠 На доработке: {rework}\n"
        f"🟡 В работе: {in_progress}\n"
        f"⌛ На утверждении: {review}\n\n"
        f"🗓 Данные обновлены: {updated_at}"
    )

    await message.answer(text)

@dp.message(F.text == "👤 Статистика по людям")
async def user_tasks_stats(message: Message):
    if not is_allowed_message(message):
        return

    if not is_admin(message.from_user.username):
        return

    rows = sheet.get_all_values()[1:]
    stats = {}

    for row in rows:
        while len(row) < 18:
            row.append("")

        if not row[0]:
            continue

        username = norm_user(row[2]) or "без_исполнителя"

        if username not in stats:
            stats[username] = {
                "total": 0,
                "done": 0,
                "overdue": 0,
                "moved": 0,
                "refused": 0,
                "waiting": 0,
                "in_progress": 0,
                "review": 0,
                "rework": 0,
            }

        status = row[6]
        comment = row[8].lower() if row[8] else ""

        stats[username]["total"] += 1

        if status == "✅ Готово":
            stats[username]["done"] += 1
        if status == "🔴 Просрочена" or row[11].lower() == "да":
            stats[username]["overdue"] += 1
        if "перенос" in comment or "перенести" in comment or "срок" in comment:
            stats[username]["moved"] += 1
        if status == "⚠️ Требует переназначения":
            stats[username]["refused"] += 1
        if status == "❓ Ожидает подтверждения":
            stats[username]["waiting"] += 1
        if status == "🔄 В работе":
            stats[username]["in_progress"] += 1
        if status == "⏳ На утверждении":
            stats[username]["review"] += 1
        if status == "✏️ На доработке":
            stats[username]["rework"] += 1

    if not stats:
        await message.answer("👤 СТАТИСТИКА ПО ЛЮДЯМ\n\nСтатистики пока нет.")
        return

    sorted_users = sorted(
        stats.items(),
        key=lambda item: item[1]["total"],
        reverse=True,
    )

    chunks = []
    current = "👤 СТАТИСТИКА ПО ЛЮДЯМ\n\nСтатистика по исполнителям\n\n"

    for username, data in sorted_users:
        block = (
            f"👤 @{username}\n\n"
            f"📄 Всего задач: {data['total']}\n"
            f"✅ Выполнено: {data['done']}\n"
            f"🔴 Просрочено: {data['overdue']}\n"
            f"🔄 Перенесено: {data['moved']}\n"
            f"❌ Отказано: {data['refused']}\n"
            f"⏳ Ожидают: {data['waiting']}\n"
            f"🛠 На доработке: {data['rework']}\n"
            f"🟡 В работе: {data['in_progress']}\n"
            f"⌛ На утверждении: {data['review']}\n"
            f"──────────────\n\n"
        )

        if len(current) + len(block) > 3500:
            chunks.append(current)
            current = ""

        current += block

    if current:
        current += f"📋 Показано исполнителей: {len(sorted_users)}"
        chunks.append(current)

    for chunk in chunks:
        await message.answer(chunk)

@dp.message(F.text == "📎 Сдать работу")
async def choose_submit(message: Message):
    if not is_allowed_message(message):
        return

    tasks = get_active_tasks_for_user(message.from_user.username)
    tasks = [t for t in tasks if t[6] in ["🔄 В работе", "✏️ На доработке"]]

    if not tasks:
        await message.answer("Нет задач, которые можно сдать")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"📎 Сдать #{row[0]} — {row[3][:25]}", callback_data=f"submit_{row[0]}")]
            for row in tasks
        ]
    )

    await message.answer("Выбери задачу:", reply_markup=keyboard)


@dp.message(F.text == "⏰ Перенести срок")
async def choose_move(message: Message):
    if not is_allowed_message(message):
        return

    tasks = get_active_tasks_for_user(message.from_user.username)

    if not tasks:
        await message.answer("У тебя нет активных задач")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⏰ Перенести #{row[0]} — {row[3][:25]}", callback_data=f"move_{row[0]}")]
            for row in tasks
        ]
    )

    await message.answer("Выбери задачу:", reply_markup=keyboard)


@dp.message(F.text == "🆘 Нужна помощь")
async def choose_help(message: Message):
    if not is_allowed_message(message):
        return

    tasks = get_active_tasks_for_user(message.from_user.username)

    if not tasks:
        await message.answer("У тебя нет активных задач")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🆘 Помощь #{row[0]} — {row[3][:25]}", callback_data=f"help_{row[0]}")]
            for row in tasks
        ]
    )

    await message.answer("Выбери задачу:", reply_markup=keyboard)


@dp.message(F.text == "❌ Отказаться")
async def choose_refuse(message: Message):
    if not is_allowed_message(message):
        return

    tasks = get_active_tasks_for_user(message.from_user.username)

    if not tasks:
        await message.answer("У тебя нет активных задач")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"❌ Отказ #{row[0]} — {row[3][:25]}", callback_data=f"refuse_{row[0]}")]
            for row in tasks
        ]
    )

    await message.answer("Выбери задачу:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data.startswith("take_"))
async def take_task(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[1]
    row_number, row = find_task(task_id)

    if not row:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    username = norm_user(callback.from_user.username)
    assignee = norm_user(row[2])

    if username != assignee:
        await callback.answer("Эту задачу может взять только исполнитель", show_alert=True)
        return

    await change_task_status(row_number, row, task_id, "🔄 В работе", callback.from_user.username)
    sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

    _, updated_row = find_task(task_id)

    await callback.message.edit_text(
        make_task_text(task_id, updated_row),
        reply_markup=work_keyboard(task_id),
    )

    await callback.answer("Задача взята ✅")


@dp.callback_query(lambda c: c.data.startswith("submit_"))
async def submit_task(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[1]
    user_states[callback.from_user.id] = {"action": "submit", "task_id": task_id}

    await callback.message.answer(
        f"Пришли ссылку на результат по задаче #{task_id}"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("move_"))
async def move_deadline(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[1]
    user_states[callback.from_user.id] = {"action": "move", "task_id": task_id}

    await callback.message.answer(
        f"Напиши новый срок и причину переноса для задачи #{task_id}.\n\n"
        f"Например:\n15.06 18:00 — жду материал"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("refuse_") or c.data.startswith("decline_"))
async def refuse_task(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[1]
    user_states[callback.from_user.id] = {"action": "refuse", "task_id": task_id}

    await callback.message.answer(
        f"Напиши причину отказа от задачи #{task_id}"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("help_"))
async def help_task(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[1]
    row_number, row = find_task(task_id)

    if not row:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    username = norm_user(callback.from_user.username)
    assignee = norm_user(row[2])

    if username != assignee:
        await callback.answer("Запросить помощь может только исполнитель", show_alert=True)
        return

    user_states[callback.from_user.id] = {
        "action": "help_request",
        "task_id": task_id,
    }

    await callback.message.answer(
        f"🆘 Напиши, что именно нужно по задаче #{task_id}, или пришли файл/фото/видео/голосовое.\n\n"
        f"Например: не хватает исходников, нужна редактура, нужен контакт героя.\n"
        f"Если отправляешь файл, можно добавить подпись к нему."
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("accept_"))
async def accept_task(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[1]
    row_number, row = find_task(task_id)

    if not row:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    username = norm_user(callback.from_user.username)
    creator = norm_user(row[1])

    if username != creator and not is_admin(username):
        await callback.answer("Только автор задачи или редактор может принять работу", show_alert=True)
        return

    await change_task_status(row_number, row, task_id, "✅ Готово", callback.from_user.username)
    sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

    _, updated_row = find_task(task_id)

    await callback.message.edit_text(make_task_text(task_id, updated_row))
    await callback.message.answer(f"✅ Задача #{task_id} выполнена")

    try:
        await bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            message_thread_id=COMPLETED_THREAD_ID,
            text=(
                f"✅ ВЫПОЛНЕННАЯ ЗАДАЧА\n\n"
                f"📋 #{task_id}\n\n"
                f"👤 Автор: @{updated_row[1]}\n"
                f"🧑‍💻 Исполнитель: {updated_row[2]}\n\n"
                f"📝 Описание:\n{updated_row[3]}\n\n"
                f"🔗 Результат:\n"
                f"{updated_row[7] if updated_row[7] else 'не указан'}\n\n"
                f"✅ Принял: @{username}\n"
                f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            ),
        )
    except Exception as e:
        logging.warning(f"Ошибка отправки в раздел выполненных задач: {e}")

    await callback.answer("Принято ✅")


@dp.callback_query(lambda c: c.data.startswith("admin_help_"))
async def admin_help(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[2]
    row_number, row = find_task(task_id)

    if not row:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    username = norm_user(callback.from_user.username)
    creator = norm_user(row[1])

    if username != creator and not is_admin(username):
        await callback.answer("Помочь может автор задачи или админ", show_alert=True)
        return

    user_states[callback.from_user.id] = {
        "action": "admin_help_reply",
        "task_id": task_id,
    }

    await callback.message.answer(
        f"Напиши комментарий помощи по задаче #{task_id} или пришли файл/фото/видео/голосовое.\n\n"
        f"Я отправлю это исполнителю {row[2]}.\n"
        f"Если отправляешь файл, можно добавить подпись к нему."
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("rework_"))
async def rework_task(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[1]
    user_states[callback.from_user.id] = {"action": "rework", "task_id": task_id}

    await callback.message.answer(
        f"Напиши комментарий правок для задачи #{task_id}"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("cancel_"))
async def cancel_task(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[1]
    row_number, row = find_task(task_id)

    if not row:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    username = norm_user(callback.from_user.username)
    creator = norm_user(row[1])

    if username != creator and not is_admin(username):
        await callback.answer("Отменить может только автор задачи или админ", show_alert=True)
        return

    await change_task_status(row_number, row, task_id, "❌ Отменена", callback.from_user.username)
    sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

    _, updated_row = find_task(task_id)

    await callback.message.edit_text(make_task_text(task_id, updated_row))
    await callback.message.answer(f"❌ Задача #{task_id} отменена")
    await callback.answer("Отменено")


@dp.callback_query(lambda c: c.data.startswith("approve_move_"))
async def approve_move(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[2]
    row_number, row = find_task(task_id)

    if not is_admin(callback.from_user.username):
        await callback.answer("Только админ может одобрить перенос", show_alert=True)
        return

    comment = row[8]
    new_deadline = extract_deadline_from_text(comment)

    if new_deadline == "не указан":
        await callback.answer("Не нашёл новый срок в комментарии", show_alert=True)
        return

    sheet.update_cell(row_number, 5, new_deadline)
    sheet.update_cell(row_number, 9, f"Перенос одобрен. Новый срок: {new_deadline}. {comment}")
    sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

    _, updated_row = find_task(task_id)
    await update_card(task_id, updated_row, None)

    await callback.message.answer(f"✅ Перенос задачи #{task_id} одобрен. Новый срок: {new_deadline}")
    await callback.answer("Одобрено")


@dp.callback_query(lambda c: c.data.startswith("deny_move_"))
async def deny_move(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[2]

    if not is_admin(callback.from_user.username):
        await callback.answer("Только админ может отклонить перенос", show_alert=True)
        return

    await callback.message.answer(f"❌ Перенос задачи #{task_id} отклонён")
    await callback.answer("Отклонено")


@dp.callback_query(lambda c: c.data.startswith("reassign_"))
async def reassign(callback: CallbackQuery):
    if not is_allowed_callback(callback):
        await callback.answer("Этот бот работает только в разделе ЗАДАЧИ", show_alert=True)
        return

    task_id = callback.data.split("_")[1]
    user_states[callback.from_user.id] = {"action": "reassign", "task_id": task_id}

    await callback.message.answer(
        f"Напиши нового исполнителя для задачи #{task_id} в формате @username"
    )
    await callback.answer()


@dp.message()
async def text_handler(message: Message):
    incoming_task_text = get_message_task_text(message)
    logging.info(f"CHAT={message.chat.id} THREAD={message.message_thread_id} TEXT={incoming_task_text}")

    if not is_allowed_message(message):
        return

    state = user_states.get(message.from_user.id)

    if state:
        task_id = state["task_id"]
        action = state["action"]
        row_number, row = find_task(task_id)

        if not row:
            await message.answer("Задача не найдена")
            user_states.pop(message.from_user.id, None)
            return

        username = norm_user(message.from_user.username)
        assignee = norm_user(row[2])
        creator = norm_user(row[1])

        if action == "submit":
            if username != assignee:
                await message.answer("Сдать задачу может только исполнитель")
                return

            result_text = get_submission_result_text(message)

            if not result_text:
                await message.answer("Пришли ссылку, текст, документ, фото, видео, аудио или голосовое сообщение")
                return

            await change_task_status(row_number, row, task_id, "⏳ На утверждении", message.from_user.username)
            sheet.update_cell(row_number, 8, result_text)
            sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

            _, updated_row = find_task(task_id)
            await update_card(task_id, updated_row, None)

            await copy_submission_to_creator(row, message, task_id)

            await message.answer(f"✅ Задача #{task_id} отправлена на утверждение")
            await notify_creator(
                updated_row,
                f"⏳ {row[2]} сдал задачу #{task_id}\nРезультат: {result_text}",
                review_keyboard(task_id),
            )

        elif action == "move":
            if username != assignee:
                await message.answer("Перенос может запросить только исполнитель")
                return

            sheet.update_cell(row_number, 9, message.text)
            sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

            await message.answer("⏰ Запрос на перенос отправлен")
            await notify_creator(
                row,
                f"⏰ {row[2]} просит перенести задачу #{task_id}\n\n"
                f"Новый срок/причина: {message.text}",
                move_request_keyboard(task_id),
            )

        elif action == "refuse":
            if username != assignee:
                await message.answer("Отказаться может только исполнитель")
                return

            await change_task_status(row_number, row, task_id, "⚠️ Требует переназначения", message.from_user.username)
            sheet.update_cell(row_number, 9, message.text)
            sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

            _, updated_row = find_task(task_id)
            await update_card(task_id, updated_row)

            await message.answer("❌ Отказ зафиксирован")
            await notify_creator(
                updated_row,
                f"❌ {row[2]} отказался от задачи #{task_id}\nПричина: {message.text}",
                reassign_keyboard(task_id),
            )

        elif action == "help_request":
            if username != assignee:
                await message.answer("Запросить помощь может только исполнитель")
                return

            help_text = get_message_content_text(message)

            if not help_text:
                await message.answer("Пришли текст, документ, фото, видео, аудио или голосовое сообщение")
                return

            old_comment = row[8] if row[8] else ""
            new_comment = f"{old_comment}\n🆘 Запрос помощи: {help_text}".strip()

            sheet.update_cell(row_number, 9, new_comment)
            sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

            _, updated_row = find_task(task_id)
            await update_card(task_id, updated_row, None)

            await copy_message_to_username(
                row[1],
                message,
                f"📎 Материал к запросу помощи по задаче #{task_id} от {row[2]}:",
            )

            await message.answer("🆘 Запрос помощи отправлен автору задачи.")

            await notify_creator(
                updated_row,
                f"🆘 ЗАПРОС ПОМОЩИ\n\n"
                f"📋 Задача #{task_id}\n\n"
                f"👤 Исполнитель: {row[2]}\n"
                f"📝 Описание:\n{row[3]}\n\n"
                f"❓ Что нужно:\n{help_text}",
                admin_help_keyboard(task_id, row[2]),
            )

        elif action == "admin_help_reply":
            if username != creator and not is_admin(username):
                await message.answer("Ответить на запрос помощи может только автор или админ")
                return

            reply_text = get_message_content_text(message)

            if not reply_text:
                await message.answer("Пришли текст, документ, фото, видео, аудио или голосовое сообщение")
                return

            old_comment = row[8] if row[8] else ""
            new_comment = f"{old_comment}\n🤝 Ответ помощи от @{username}: {reply_text}".strip()

            sheet.update_cell(row_number, 9, new_comment)
            sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

            _, updated_row = find_task(task_id)
            await update_card(task_id, updated_row, None)

            await copy_message_to_username(
                row[2],
                message,
                f"📎 Материал помощи по задаче #{task_id} от @{username}:",
            )

            await notify_user_by_username(
                row[2],
                f"🤝 ПОМОЩЬ ПО ЗАДАЧЕ\n\n"
                f"📋 Задача #{task_id}\n\n"
                f"👤 От: @{username}\n\n"
                f"💬 Комментарий:\n{reply_text}",
            )

            await message.answer(f"🤝 Помощь отправлена исполнителю {row[2]}.")

        elif action == "rework":
            if username != creator and not is_admin(username):
                await message.answer("Правки может отправить только автор или админ")
                return

            await change_task_status(row_number, row, task_id, "✏️ На доработке", message.from_user.username)
            sheet.update_cell(row_number, 9, message.text)
            sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

            _, updated_row = find_task(task_id)
            await update_card(task_id, updated_row, None)

            await message.answer(f"✏️ Задача #{task_id} отправлена на доработку")

        elif action == "reassign":
            if not is_admin(username):
                await message.answer("Переназначить может только админ")
                return

            new_assignee = message.text.strip()

            if not new_assignee.startswith("@"):
                await message.answer("Нужен username в формате @username")
                return

            sheet.update_cell(row_number, 3, new_assignee)
            row[2] = new_assignee
            await change_task_status(row_number, row, task_id, "❓ Ожидает подтверждения", message.from_user.username)
            sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

            _, updated_row = find_task(task_id)
            await update_card(task_id, updated_row, None)

            await message.answer(f"🔄 Задача #{task_id} переназначена на {new_assignee}")

        user_states.pop(message.from_user.id, None)
        return

    task_text_for_parse = incoming_task_text
    if task_text_for_parse.startswith("/задача"):
        task_text_for_parse = re.sub(r"^/задача(@\w+)?", "", task_text_for_parse).strip()

    if task_text_for_parse and "@" in task_text_for_parse and not (incoming_task_text.startswith("/") and not incoming_task_text.startswith("/задача")):
        parsed = parse_free_task(task_text_for_parse)

        if parsed:
            assignee, description, deadline, link = parsed
            task_id = await create_task_from_parts(
                message,
                assignee,
                description,
                deadline,
                link
            )
            if has_copyable_attachment(message):
                await message.answer(f"✅ Создал задачу #{task_id} и отправил вложение исполнителю")
            else:
                await message.answer(f"✅ Создал задачу #{task_id}")
            return

    await message.answer(
        "Я не понял сообщение.\n\n"
        "Используй кнопки меню или напиши задачу с @исполнителем. "
        "Файл/фото/видео можно прикрепить к сообщению и написать задачу в подписи."
    )


async def deadline_checker():
    await asyncio.sleep(10)

    while True:
        try:
            rows = sheet.get_all_values()

            for index, row in enumerate(rows[1:], start=2):
                while len(row) < 18:
                    row.append("")

                task_id = row[0]
                status = row[6]

                if not task_id or status in ["✅ Готово", "❌ Отменена"]:
                    continue

                deadline_dt = parse_deadline_to_datetime(row[4])

                if not deadline_dt:
                    continue

                now = datetime.now()
                seconds_left = (deadline_dt - now).total_seconds()

                reminder_36_sent = row[14]
                reminder_12_sent = row[15]
                reminder_1h_sent = row[16]
                overdue_notice_sent = row[17]

                if 0 < seconds_left <= 36 * 60 * 60 and reminder_36_sent != "да":
                    await notify_user_by_username(
                        row[2],
                        f"⏰ Напоминание: дедлайн задачи #{task_id} через 36 часов или меньше.\n\n"
                        f"Описание: {row[3]}\n"
                        f"Дедлайн: {row[4]}",
                    )
                    sheet.update_cell(index, REMINDER_36_COL, "да")

                if 0 < seconds_left <= 12 * 60 * 60 and reminder_12_sent != "да":
                    await notify_user_by_username(
                        row[2],
                        f"⏰ Напоминание: дедлайн задачи #{task_id} через 12 часов или меньше.\n\n"
                        f"Описание: {row[3]}\n"
                        f"Дедлайн: {row[4]}",
                    )
                    sheet.update_cell(index, REMINDER_12_COL, "да")

                if 0 < seconds_left <= 60 * 60 and reminder_1h_sent != "да":
                    await notify_user_by_username(
                        row[2],
                        f"⚠️ Дедлайн задачи #{task_id} через час или меньше!\n\n"
                        f"Описание: {row[3]}\n"
                        f"Дедлайн: {row[4]}",
                    )
                    await notify_creator(
                        row,
                        f"⚠️ У задачи #{task_id} дедлайн через час или меньше.\n\n"
                        f"Исполнитель: {row[2]}\n"
                        f"Описание: {row[3]}\n"
                        f"Дедлайн: {row[4]}",
                    )
                    sheet.update_cell(index, REMINDER_1H_COL, "да")

                if seconds_left <= 0 and overdue_notice_sent != "да":
                    await change_task_status(index, row, task_id, "🔴 Просрочена", "deadline_checker")
                    sheet.update_cell(index, 12, "да")
                    sheet.update_cell(index, OVERDUE_NOTICE_COL, "да")
                    sheet.update_cell(index, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

                    _, updated_row = find_task(task_id)
                    if updated_row:
                        await update_card(task_id, updated_row, None)

                    await notify_user_by_username(
                        row[2],
                        f"🔴 Задача #{task_id} просрочена.\n\n"
                        f"Описание: {row[3]}\n"
                        f"Дедлайн: {row[4]}",
                    )
                    await notify_creator(
                        row,
                        f"🔴 Задача #{task_id} просрочена.\n\n"
                        f"Исполнитель: {row[2]}\n"
                        f"Описание: {row[3]}\n"
                        f"Дедлайн: {row[4]}",
                    )

        except Exception as e:
            logging.exception(f"Ошибка проверки дедлайнов: {e}")

        await asyncio.sleep(15 * 60)


async def main():
    asyncio.create_task(deadline_checker())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())