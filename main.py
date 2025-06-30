import logging
import os
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
import requests
from bs4 import BeautifulSoup

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Store user data and jobs in dictionaries
user_data = {}
jobs = {}

# States for conversations
(
    TASK_NAME,
    URL,
    SELECTOR,
    SELECT_TASK_TO_DELETE,
    SELECT_TASK_TO_UPDATE,
    UPDATE_URL,
    UPDATE_SELECTOR,
) = range(7)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and instructions."""
    await update.message.reply_text(
        "Hi! I'm a website monitoring bot.\n\n"
        "You can control me by sending these commands:\n\n"
        "/add - add a new website to monitor\n"
        "/list - list your current monitoring tasks\n"
        "/update - update an existing task\n"
        "/delete - delete a task\n"
        "/cancel - cancel the current operation"
    )


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation to add a task."""
    await update.message.reply_text("Please give a name for your new task.")
    return TASK_NAME


async def receive_task_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives task name and asks for URL."""
    user_id = update.message.from_user.id
    task_name = update.message.text

    if user_id not in user_data:
        user_data[user_id] = {"tasks": {}}
    if user_id not in jobs:
        jobs[user_id] = {}

    if task_name in user_data[user_id]["tasks"]:
        await update.message.reply_text(
            "This task name already exists. Please choose another one."
        )
        return TASK_NAME

    context.user_data["task_name"] = task_name
    user_data[user_id]["tasks"][task_name] = {}

    await update.message.reply_text(
        "Great! Now, please provide the URL of the website."
    )
    return URL


async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives URL and asks for selector."""
    user_id = update.message.from_user.id
    task_name = context.user_data["task_name"]
    url = update.message.text
    user_data[user_id]["tasks"][task_name]["url"] = url

    await update.message.reply_text(
        "Thanks. Now, please provide the CSS selector for the element."
    )
    return SELECTOR


async def receive_selector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives selector and sets up monitoring."""
    user_id = update.message.from_user.id
    task_name = context.user_data["task_name"]
    selector = update.message.text
    user_data[user_id]["tasks"][task_name]["selector"] = selector

    success, message = await setup_monitoring_task(context, user_id, task_name)
    await update.message.reply_text(message)

    if success:
        del context.user_data["task_name"]
        return ConversationHandler.END
    else:
        # if setup fails, we should clean up
        del user_data[user_id]["tasks"][task_name]
        if not user_data[user_id]["tasks"]:
            del user_data[user_id]
        await update.message.reply_text("Please try adding the task again with /add.")
        return ConversationHandler.END


async def setup_monitoring_task(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, task_name: str
) -> tuple[bool, str]:
    """Sets up or updates a monitoring job."""
    task = user_data[user_id]["tasks"][task_name]
    url = task.get("url")
    selector = task.get("selector")

    if not url or not selector:
        return False, "Something went wrong. URL or selector not found."

    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.content, "html.parser")
        element = soup.select_one(selector)

        if element:
            initial_state = str(element)
            user_data[user_id]["tasks"][task_name]["initial_state"] = initial_state

            # Schedule the job
            job = context.job_queue.run_repeating(
                check_website,
                interval=60,
                first=0,
                data={"user_id": user_id, "task_name": task_name},
            )
            jobs[user_id][task_name] = job
            logger.info(
                f"Initial state for {url} with selector '{selector}': {initial_state}"
            )
            return (
                True,
                f"Monitoring has started for task '{task_name}'.\n"
                f"I will check for changes every 60 seconds.",
            )
        else:
            return (
                False,
                "Could not find the element with the given CSS selector. Please try again.",
            )

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        return (
            False,
            "An error occurred while trying to fetch the URL. Please ensure it is correct and try again.",
        )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists all monitoring tasks for the user."""
    user_id = update.message.from_user.id
    if user_id not in user_data or not user_data[user_id].get("tasks"):
        await update.message.reply_text("You have no active monitoring tasks.")
        return

    tasks = user_data[user_id]["tasks"]
    if not tasks:
        await update.message.reply_text("You have no active monitoring tasks.")
        return

    message = "Your monitoring tasks:\n\n"
    for name, details in tasks.items():
        message += f"Task: {name}\n"
        message += f"  URL: {details['url']}\n"
        message += f"  Selector: {details['selector']}\n\n"

    await update.message.reply_text(message)


async def delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation to delete a task."""
    user_id = update.message.from_user.id
    if user_id not in user_data or not user_data[user_id].get("tasks"):
        await update.message.reply_text("You have no tasks to delete.")
        return ConversationHandler.END

    tasks = list(user_data[user_id]["tasks"].keys())
    if not tasks:
        await update.message.reply_text("You have no tasks to delete.")
        return ConversationHandler.END

    await update.message.reply_text(
        f"Which task do you want to delete? Your tasks are:\n{', '.join(tasks)}"
    )
    return SELECT_TASK_TO_DELETE


async def receive_task_to_delete(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Deletes the specified task."""
    user_id = update.message.from_user.id
    task_name = update.message.text

    if user_id in jobs and task_name in jobs.get(user_id, {}):
        jobs[user_id][task_name].schedule_removal()
        del jobs[user_id][task_name]
        if not jobs[user_id]:
            del jobs[user_id]

    if user_id in user_data and task_name in user_data.get(user_id, {}).get("tasks", {}):
        del user_data[user_id]["tasks"][task_name]
        if not user_data[user_id]["tasks"]:
            del user_data[user_id]
        await update.message.reply_text(f"Task '{task_name}' has been stopped and deleted.")
    else:
        await update.message.reply_text(f"Task '{task_name}' not found.")

    return ConversationHandler.END


async def update_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation to update a task."""
    user_id = update.message.from_user.id
    if user_id not in user_data or not user_data[user_id].get("tasks"):
        await update.message.reply_text("You have no tasks to update.")
        return ConversationHandler.END

    tasks = list(user_data[user_id]["tasks"].keys())
    if not tasks:
        await update.message.reply_text("You have no tasks to update.")
        return ConversationHandler.END

    await update.message.reply_text(
        f"Which task do you want to update? Your tasks are:\n{', '.join(tasks)}"
    )
    return SELECT_TASK_TO_UPDATE


async def select_task_to_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receives task name to update and asks for new URL."""
    user_id = update.message.from_user.id
    task_name = update.message.text

    if user_id not in user_data or task_name not in user_data[user_id]["tasks"]:
        await update.message.reply_text(
            f"Task '{task_name}' not found. Please try again or /cancel."
        )
        return SELECT_TASK_TO_UPDATE

    context.user_data["task_to_update"] = task_name
    old_url = user_data[user_id]["tasks"][task_name]["url"]
    await update.message.reply_text(
        f"The current URL is: {old_url}\nPlease send the new URL, or send 'skip' to keep it."
    )
    return UPDATE_URL


async def receive_new_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives new URL and asks for new selector."""
    user_id = update.message.from_user.id
    task_name = context.user_data["task_to_update"]
    new_url = update.message.text

    if new_url.lower() != "skip":
        user_data[user_id]["tasks"][task_name]["url"] = new_url

    old_selector = user_data[user_id]["tasks"][task_name]["selector"]
    await update.message.reply_text(
        f"The current selector is: {old_selector}\nPlease send the new selector, or send 'skip' to keep it."
    )
    return UPDATE_SELECTOR


async def receive_new_selector_and_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receives new selector and updates the task."""
    user_id = update.message.from_user.id
    task_name = context.user_data["task_to_update"]
    new_selector = update.message.text

    if new_selector.lower() != "skip":
        user_data[user_id]["tasks"][task_name]["selector"] = new_selector

    # Stop old job
    if user_id in jobs and task_name in jobs.get(user_id, {}):
        jobs[user_id][task_name].schedule_removal()
        del jobs[user_id][task_name]

    # Start new job
    success, message = await setup_monitoring_task(context, user_id, task_name)
    await update.message.reply_text(f"Task '{task_name}' updated. {message}")

    del context.user_data["task_to_update"]
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    user_id = update.message.from_user.id
    if "task_name" in context.user_data:
        task_name = context.user_data["task_name"]
        # Clean up partially created task
        if (
            user_id in user_data
            and task_name in user_data[user_id]["tasks"]
            and not user_data[user_id]["tasks"][task_name]
        ):
            del user_data[user_id]["tasks"][task_name]
        del context.user_data["task_name"]

    if "task_to_update" in context.user_data:
        del context.user_data["task_to_update"]

    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


async def check_website(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Checks the website for changes."""
    job_data = context.job.data
    user_id = job_data["user_id"]
    task_name = job_data["task_name"]

    if user_id not in user_data or task_name not in user_data[user_id]["tasks"]:
        logger.warning(f"Task {task_name} for user {user_id} not found in user_data. Stopping job.")
        context.job.schedule_removal()
        return

    task = user_data[user_id]["tasks"][task_name]
    url = task.get("url")
    selector = task.get("selector")
    initial_state = task.get("initial_state")

    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.content, "html.parser")
        current_element = soup.select_one(selector)

        if current_element:
            current_state = str(current_element)
            if current_state != initial_state:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"The content of task '{task_name}' has changed!\n"
                    f"URL: {url}",
                )
                user_data[user_id]["tasks"][task_name][
                    "initial_state"
                ] = current_state  # Update the state
        else:
            logger.warning(
                f"Element with selector '{selector}' no longer found on {url} for task '{task_name}'"
            )

    except Exception as e:
        logger.error(f"Error checking website for user {user_id}, task '{task_name}': {e}")



def main() -> None:
    """Start the bot."""
    application = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    # Conversation handler for adding a task
    add_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_task)],
        states={
            TASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_task_name)],
            URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url)],
            SELECTOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_selector)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    # Conversation handler for deleting a task
    delete_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("delete", delete_task)],
        states={
            SELECT_TASK_TO_DELETE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_task_to_delete)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    # Conversation handler for updating a task
    update_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("update", update_task)],
        states={
            SELECT_TASK_TO_UPDATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_task_to_update)
            ],
            UPDATE_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_url)
            ],
            UPDATE_SELECTOR: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, receive_new_selector_and_update
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_tasks))
    application.add_handler(add_conv_handler)
    application.add_handler(delete_conv_handler)
    application.add_handler(update_conv_handler)

    # Run the bot
    application.run_polling()


if __name__ == "__main__":
    main()
