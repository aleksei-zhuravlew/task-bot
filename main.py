import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from config import BOT_TOKEN, SPREADSHEET_NAME

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)

spreadsheet = client.open(SPREADSHEET_NAME)
sheet = spreadsheet.worksheet("tasks")
admins_sheet = spreadsheet.worksheet("admins")


def norm_user(username):
    if not username:
        return ""
    return username.replace("@", "").strip()


def get_admins():
    values = admins_sheet.col_values(1)[1:]
    return [norm_user(x) for x in values if x.strip()]


def is_admin(username):
    return norm_user(username) in get_admins()


def find_task(task_id):
    values = sheet.get_all_values()
    for index, row in enumerate(values):
        if row and row[0] == str(task_id):
            while len(row) < 14:
                row.append("")
            return index + 1, row
    return None, None


def active_status(status):
    return status not in ["✅ Готово", "❌ Отменена"]


def waiting_keyboard(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Беру", callback_data=f"take_{task_id}"),
        InlineKeyboardButton(text="❌ Не могу", callback_data=f"decline_{task_id}"),
        InlineKeyboardButton(text="✏️ Сдвинуть", callback_data=f"move_{task_id}"),
    ]])


def work_keyboard(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📎 Как сдать", callback_data=f"how_submit_{task_id}"),
        InlineKeyboardButton(text="🆘 Нужна помощь", callback_data=f"help_{task_id}"),
        InlineKeyboardButton(text="❌ Отказаться", callback_data=f"refuse_{task_id}"),
    ]])


def review_keyboard(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принято", callback_data=f"accept_{task_id}"),
        InlineKeyboardButton(text="✏️ На правки", callback_data=f"rework_{task_id}"),
        InlineKeyboardButton(text="🗑️ Отменить", callback_data=f"cancel_{task_id}"),
    ]])


def final_keyboard():
    return None


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
        text += f"\nПравки: {row[8]}"

    return text


async def update_card(task_id, row, keyboard=None):
    chat_id = row[12]
    message_id = row[13]

    if chat_id and message_id:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=int(message_id),
            text=make_task_text(task_id, row),
            reply_markup=keyboard,
        )


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("Бот запущен ✅")


@dp.message(Command("задача"))
async def create_task(message: Message):
    text = message.text.replace("/задача", "").strip()

    if not text:
        await message.answer("Пример:\n/задача @user Сделать обложку | 09.06 | ссылка")
        return

    try:
        parts = text.split("|")

        left = parts[0].strip()
        deadline = parts[1].strip()
        link = parts[2].strip()

        words = left.split()
        assignee = words[0]
        description = " ".join(words[1:])

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
            "",
            "",
            "нет",
            "",
            "",
        ]

        sheet.append_row(row)

        sent = await message.answer(
            make_task_text(task_id, [str(x) for x in row]),
            reply_markup=waiting_keyboard(task_id),
        )

        row_number, saved_row = find_task(task_id)
        sheet.update_cell(row_number, 13, sent.chat.id)
        sheet.update_cell(row_number, 14, sent.message_id)

    except Exception as e:
        await message.answer(f"Ошибка: {e}")


@dp.message(Command("мои"))
async def my_tasks(message: Message):
    username = norm_user(message.from_user.username)

    values = sheet.get_all_values()[1:]
    result = "📋 Твои активные задачи:\n\n"
    found = False

    for row in values:
        while len(row) < 14:
            row.append("")

        assignee = norm_user(row[2])

        if assignee == username and active_status(row[6]):
            found = True
            result += (
                f"#{row[0]} — {row[3]}\n"
                f"Дедлайн: {row[4]}\n"
                f"Статус: {row[6]}\n"
                f"Материал: {row[5]}\n\n"
            )

    if not found:
        result = "У тебя нет активных задач ✅"

    await message.answer(result)


@dp.message(Command("сдать"))
async def submit_by_command(message: Message):
    text = message.text.replace("/сдать", "").strip()
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer("Формат:\n/сдать 3 https://ссылка-на-результат")
        return

    task_id = parts[0]
    result_link = parts[1]

    row_number, row = find_task(task_id)

    if not row:
        await message.answer("Задача не найдена")
        return

    username = norm_user(message.from_user.username)
    assignee = norm_user(row[2])

    if username != assignee:
        await message.answer("Сдать задачу может только исполнитель")
        return

    if row[6] not in ["🔄 В работе", "✏️ На доработке"]:
        await message.answer("Эту задачу сейчас нельзя сдать")
        return

    sheet.update_cell(row_number, 7, "⏳ На утверждении")
    sheet.update_cell(row_number, 8, result_link)

    _, updated_row = find_task(task_id)

    await update_card(task_id, updated_row, review_keyboard(task_id))
    await message.answer(f"✅ Задача #{task_id} отправлена на утверждение")


@dp.message(Command("правки"))
async def rework_by_command(message: Message):
    text = message.text.replace("/правки", "").strip()
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer("Формат:\n/правки 3 Что нужно исправить")
        return

    task_id = parts[0]
    comment = parts[1]

    row_number, row = find_task(task_id)

    if not row:
        await message.answer("Задача не найдена")
        return

    username = norm_user(message.from_user.username)
    creator = norm_user(row[1])

    if username != creator and not is_admin(username):
        await message.answer("Правки может отправить только автор задачи или админ")
        return

    sheet.update_cell(row_number, 7, "✏️ На доработке")
    sheet.update_cell(row_number, 9, comment)

    _, updated_row = find_task(task_id)

    await update_card(task_id, updated_row, work_keyboard(task_id))
    await message.answer(f"✏️ Задача #{task_id} отправлена на доработку")


@dp.callback_query(lambda c: c.data.startswith("take_"))
async def take_task(callback: CallbackQuery):
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

    if row[6] == "🔄 В работе":
        await callback.answer("Задача уже в работе", show_alert=True)
        return

    sheet.update_cell(row_number, 7, "🔄 В работе")

    _, updated_row = find_task(task_id)

    await callback.message.edit_text(
        make_task_text(task_id, updated_row),
        reply_markup=work_keyboard(task_id),
    )

    await callback.answer("Задача взята ✅")


@dp.callback_query(lambda c: c.data.startswith("how_submit_"))
async def how_submit(callback: CallbackQuery):
    task_id = callback.data.split("_")[2]

    await callback.answer(
        f"Чтобы сдать работу, напиши:\n/сдать {task_id} ссылка",
        show_alert=True,
    )


@dp.callback_query(lambda c: c.data.startswith("accept_"))
async def accept_task(callback: CallbackQuery):
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

    _, updated_row = find_task(task_id)

    await callback.message.edit_text(
        make_task_text(task_id, updated_row),
        reply_markup=final_keyboard(),
    )

    await callback.message.answer(f"✅ Задача #{task_id} выполнена")
    await callback.answer("Принято ✅")


@dp.callback_query(lambda c: c.data.startswith("rework_"))
async def rework_task(callback: CallbackQuery):
    task_id = callback.data.split("_")[1]

    await callback.answer(
        f"Чтобы отправить правки, напиши:\n/правки {task_id} текст правок",
        show_alert=True,
    )


@dp.callback_query(lambda c: c.data.startswith("cancel_"))
async def cancel_task(callback: CallbackQuery):
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

    _, updated_row = find_task(task_id)

    await callback.message.edit_text(
        make_task_text(task_id, updated_row),
        reply_markup=final_keyboard(),
    )

    await callback.message.answer(f"❌ Задача #{task_id} отменена")
    await callback.answer("Отменено")


@dp.callback_query(lambda c: c.data.startswith("help_"))
async def help_task(callback: CallbackQuery):
    task_id = callback.data.split("_")[1]
    row_number, row = find_task(task_id)

    if not row:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    await callback.message.answer(
        f"🆘 Задаче #{task_id} {row[2]} нужна помощь. Кто может подключиться?"
    )
    await callback.answer("Сообщение отправлено")


@dp.callback_query(lambda c: c.data.startswith("refuse_") or c.data.startswith("decline_"))
async def refuse_task(callback: CallbackQuery):
    task_id = callback.data.split("_")[1]
    row_number, row = find_task(task_id)

    if not row:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    sheet.update_cell(row_number, 7, "⚠️ Требует переназначения")

    _, updated_row = find_task(task_id)

    await callback.message.edit_text(
        make_task_text(task_id, updated_row),
        reply_markup=None,
    )

    await callback.message.answer(f"⚠️ Задача #{task_id} требует переназначения")
    await callback.answer("Отказ зафиксирован")


@dp.callback_query(lambda c: c.data.startswith("move_"))
async def move_deadline(callback: CallbackQuery):
    await callback.answer("Сдвиг дедлайна добавим следующим шагом", show_alert=True)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())