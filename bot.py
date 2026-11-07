import os
import re
import logging
from urllib.parse import quote_plus, urlparse

import aiohttp
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vasixzz")

BOT_TOKEN = os.environ["BOT_TOKEN"]
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "8"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

tg = Application.builder().token(BOT_TOKEN).build()
app = FastAPI()

def clean_text(value: str) -> str:
    value = re.sub(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", "[email скрыт]", value)
    value = re.sub(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)", "[номер скрыт]", value)
    return value[:450]

async def search_public(query: str):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    headers = {
        "User-Agent": "Mozilla/5.0 (Android 15; Mobile) AppleWebKit/537.36 Chrome/130 Safari/537.36",
        "Accept-Language": "ru,en;q=0.8",
    }
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            html = await response.text()
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for item in soup.select(".result"):
        a = item.select_one(".result__a")
        snippet = item.select_one(".result__snippet")
        if not a or not a.get("href"):
            continue
        link = a.get("href")
        title = a.get_text(" ", strip=True)
        desc = snippet.get_text(" ", strip=True) if snippet else ""
        host = urlparse(link).netloc
        if link and title:
            found.append({"title": title, "url": link, "host": host, "snippet": clean_text(desc)})
        if len(found) >= MAX_RESULTS:
            break
    return found

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "VASIXZZ OSINT BOT V1.3\\n"
        "Поиск только по открытым публичным страницам.\\n\\n"
        "Команда: /scan username\\n"
        "Пример: /scan example"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправь /scan username. Бот ищет упоминания в публичной выдаче. "
        "Он не получает скрытые номера, IP, переписки, пароли или закрытые данные."
    )

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /scan username")
        return
    username = " ".join(context.args).strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,40}", username):
        await update.message.reply_text("Некорректный username. Используй 2–40 символов: буквы, цифры, . _ -")
        return

    status = await update.message.reply_text(f"Ищу публичные упоминания: {username}…")
    queries = [f'"{username}"', f'"@{username}"', f'{username} profile']
    all_results, seen = [], set()
    try:
        for q in queries:
            for item in await search_public(q):
                if item["url"] not in seen:
                    seen.add(item["url"])
                    all_results.append(item)
                if len(all_results) >= MAX_RESULTS:
                    break
            if len(all_results) >= MAX_RESULTS:
                break
        if not all_results:
            await status.edit_text("Ничего не найдено в доступной публичной выдаче.")
            return
        lines = [f"Результаты по {username} (публичный поиск):"]
        for i, item in enumerate(all_results, 1):
            lines.append(f"\\n{i}. {item['title']}\\n{item['host']}\\n{item['url']}")
            if item["snippet"]:
                lines.append(item["snippet"])
        lines.append("\\nВажно: совпадение username не доказывает, что страницы принадлежат одному человеку.")
        output = "\\n".join(lines)
        await status.edit_text(output[:4000], disable_web_page_preview=True)
    except Exception as exc:
        log.exception("Search failed")
        await status.edit_text(
            "Поиск сейчас не сработал. DuckDuckGo может ограничивать автоматические запросы. "
            "Попробуй позже. Ошибка: " + type(exc).__name__
        )

tg.add_handler(CommandHandler("start", start))
tg.add_handler(CommandHandler("help", help_cmd))
tg.add_handler(CommandHandler("scan", scan))

@app.get("/")
async def root():
    return {"status": "ok", "service": "VASIXZZ OSINT BOT", "version": "1.3-no-api"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    if WEBHOOK_SECRET:
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if supplied != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Forbidden")
    data = await request.json()
    update = Update.de_json(data, tg.bot)
    await tg.process_update(update)
    return {"ok": True}

@app.on_event("startup")
async def startup():
    await tg.initialize()
    await tg.start()
    external_url = os.getenv("RENDER_EXTERNAL_URL")
    if external_url:
        await tg.bot.set_webhook(
            url=f"{external_url}/telegram-webhook",
            secret_token=WEBHOOK_SECRET or None,
            allowed_updates=Update.ALL_TYPES,
        )
    log.info("Bot started")

@app.on_event("shutdown")
async def shutdown():
    await tg.bot.delete_webhook()
    await tg.stop()
    await tg.shutdown()
