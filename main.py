import asyncio
import logging
import re
from datetime import datetime, timedelta

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
    return username.replace("@", "").strip()


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
            while len(row) < 14:
                row.append("")
            return index + 1, row
    return None, None


def get_active_tasks_for_user(username):
    username = norm_user(username)
    result = []

    for row in sheet.get_all_values()[1:]:
        while len(row) < 14:
            row.append("")

        if norm_user(row[2]) == username and active_status(row[6]):
            result.append(row)

    return result


def get_active_tasks():
    result = []

    for row in sheet.get_all_values()[1:]:
        while len(row) < 14:
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


def parse_free_task(text):
    assignee_match = re.search(r"@[\w\d_]+", text)
    link_match = re.search(r"https?://\S+", text)

    if not assignee_match:
        return None

    assignee = assignee_match.group(0)
    link = link_match.group(0) if link_match else ""

    deadline = "не указан"

    today = datetime.now()

    if "сегодня" in text.lower():
        deadline = today.strftime("%d.%m")
    elif "завтра" in text.lower():
        deadline = (today + timedelta(days=1)).strftime("%d.%m")
    else:
        date_match = re.search(r"\b\d{1,2}\.\d{1,2}(?:\.\d{4})?\b", text)
        if date_match:
            deadline = date_match.group(0)

    description = text
    description = description.replace(assignee, "")
    if link:
        description = description.replace(link, "")
    description = re.sub(r"\b(до|к|дедлайн|задача|для)\b", "", description, flags=re.IGNORECASE)
    description = re.sub(r"\s+", " ", description).strip(" :-—")

    return assignee, description, deadline, link


async def create_task_from_parts(message, assignee, description, deadline, link):
    task_id = len(sheet.get_all_values())

    row = [
        task_id,
        norm_user(message.from_user.username),
        assignee,
        description,
        deadline,
        link,
        "❓ Ожидает подтверждения",
        "",
        "",
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        "нет",
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
                f"Материал: {link if link else 'не указан'}",
                reply_markup=waiting_keyboard(task_id),
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
            "/задача @user описание | 09.06 | ссылка\n\n"
            "Или свободно:\n"
            "@user сделать обложку до завтра https://..."
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
        await message.answer(
            make_task_text(row[0], row),
            reply_markup=task_action_keyboard(row[0]),
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

    sheet.update_cell(row_number, 7, "🔄 В работе")
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
        f"Например:\n15.06 — жду материал"
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

    await callback.message.answer(
        f"🆘 Задаче #{task_id} {row[2]} нужна помощь.\n"
        f"Описание: {row[3]}"
    )

    await notify_creator(
        row,
        f"🆘 {row[2]} просит помощь по задаче #{task_id}\n\n{row[3]}"
    )

    await callback.answer("Запрос отправлен")


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

    sheet.update_cell(row_number, 7, "✅ Готово")
    sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

    _, updated_row = find_task(task_id)

    await callback.message.edit_text(make_task_text(task_id, updated_row))
    await callback.message.answer(f"✅ Задача #{task_id} выполнена")
    await callback.answer("Принято ✅")


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

    sheet.update_cell(row_number, 7, "❌ Отменена")
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
    date_match = re.search(r"\b\d{1,2}\.\d{1,2}(?:\.\d{4})?\b", comment)

    if not date_match:
        await callback.answer("Не нашёл новую дату в комментарии", show_alert=True)
        return

    new_deadline = date_match.group(0)

    sheet.update_cell(row_number, 5, new_deadline)
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
    logging.info(f"CHAT={message.chat.id} THREAD={message.message_thread_id} TEXT={message.text}")

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

            sheet.update_cell(row_number, 7, "⏳ На утверждении")
            sheet.update_cell(row_number, 8, message.text)
            sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

            _, updated_row = find_task(task_id)
            await update_card(task_id, updated_row, None)

            await message.answer(f"✅ Задача #{task_id} отправлена на утверждение")
            await notify_creator(
                updated_row,
                f"⏳ {row[2]} сдал задачу #{task_id}\nРезультат: {message.text}",
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

            sheet.update_cell(row_number, 7, "⚠️ Требует переназначения")
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

        elif action == "rework":
            if username != creator and not is_admin(username):
                await message.answer("Правки может отправить только автор или админ")
                return

            sheet.update_cell(row_number, 7, "✏️ На доработке")
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
            sheet.update_cell(row_number, 7, "❓ Ожидает подтверждения")
            sheet.update_cell(row_number, 11, datetime.now().strftime("%d.%m.%Y %H:%M"))

            _, updated_row = find_task(task_id)
            await update_card(task_id, updated_row, None)

            await message.answer(f"🔄 Задача #{task_id} переназначена на {new_assignee}")

        user_states.pop(message.from_user.id, None)
        return

    if message.text and "@" in message.text and not message.text.startswith("/"):
        parsed = parse_free_task(message.text)

        if parsed:
            assignee, description, deadline, link = parsed
            task_id = await create_task_from_parts(
                message,
                assignee,
                description,
                deadline,
                link
            )
            await message.answer(f"✅ Создал задачу #{task_id}")
            return

    await message.answer(
        "Я не понял сообщение.\n\n"
        "Используй кнопки меню или напиши задачу с @исполнителем."
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())