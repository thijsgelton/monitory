import logging
import os
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and instructions."""
    await update.message.reply_text(
        "Hi! I am a website monitoring bot. To start monitoring, please send me the URL of the website you want to track."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming messages to set up the monitoring."""
    user_id = update.message.from_user.id
    text = update.message.text

    if "url" not in user_data.get(user_id, {}):
        user_data[user_id] = {"url": text}
        await update.message.reply_text(
            "Great! Now, please provide the CSS selector for the element you want to monitor."
        )
    elif "selector" not in user_data[user_id]:
        user_data[user_id]["selector"] = text
        await setup_monitoring(update, context)


async def setup_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sets up the monitoring job."""
    user_id = update.message.from_user.id
    url = user_data[user_id].get("url")
    selector = user_data[user_id].get("selector")

    if not url or not selector:
        await update.message.reply_text(
            "Something went wrong. Please start over with /start."
        )
        return

    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.content, "html.parser")
        element = soup.select_one(selector)

        if element:
            initial_state = str(element)
            user_data[user_id]["initial_state"] = initial_state
            await update.message.reply_text(
                f"Monitoring has started for:\n"
                f"URL: {url}\n"
                f"Selector: {selector}\n"
                f"Initial state: {initial_state}\n"
                f"I will check for changes every 60 seconds. Use /stop to cancel."
            )

            # Schedule the job
            job = context.job_queue.run_repeating(
                check_website, interval=60, first=0, user_id=user_id, data=user_id
            )
            jobs[user_id] = job
            logger.info(
                f"Initial state for {url} with selector '{selector}': {initial_state}"
            )
        else:
            await update.message.reply_text(
                "Could not find the element with the given CSS selector. Please try again."
            )
            del user_data[user_id]

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        await update.message.reply_text(
            "An error occurred while trying to fetch the URL. Please ensure it is correct and try again."
        )
        del user_data[user_id]


async def check_website(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Checks the website for changes."""
    user_id = context.job.user_id
    url = user_data[user_id].get("url")
    selector = user_data[user_id].get("selector")
    initial_state = user_data[user_id].get("initial_state")

    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.content, "html.parser")
        current_element = soup.select_one(selector)

        if current_element:
            current_state = str(current_element)
            if current_state != initial_state:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"The content of the element you are monitoring has changed!\n"
                    f"URL: {url}",
                )
                user_data[user_id]["initial_state"] = current_state  # Update the state
        else:
            logger.warning(
                f"Element with selector '{selector}' no longer found on {url}"
            )

    except Exception as e:
        logger.error(f"Error checking website for user {user_id}: {e}")


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stops the monitoring job for the user."""
    user_id = update.message.from_user.id
    if user_id in jobs:
        job = jobs[user_id]
        job.schedule_removal()
        del jobs[user_id]
        del user_data[user_id]
        await update.message.reply_text("Monitoring has been stopped.")
    else:
        await update.message.reply_text("You have no active monitoring jobs.")


def main() -> None:
    """Start the bot."""
    application = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Run the bot
    application.run_polling()


if __name__ == "__main__":
    main()
