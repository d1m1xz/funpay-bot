"""
FunPay_AutoResponder — Telegram Bot версия
Управление через @funpay_myboy_bot
"""

import os
import re
import json
import logging
import requests
from datetime import datetime
from threading import Thread, Lock
from flask import Flask, jsonify, request
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from fake_useragent import UserAgent

load_dotenv()

FUNPAY_EMAIL = os.getenv("FUNPAY_EMAIL", "")
FUNPAY_PASSWORD = os.getenv("FUNPAY_PASSWORD", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

PORT = int(os.getenv("PORT", 8080))
CHECK_INTERVAL = 15

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ua = UserAgent()
processed_messages = set()
message_lock = Lock()

# ========== TELEGRAM ==========
def tg_send(text, disable_notification=False):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
                            "disable_notification": disable_notification}, timeout=10)
    except Exception as e:
        logger.error(f"TG: {e}")

def tg_get_updates(offset=0):
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                         params={"offset": offset, "timeout": 5}, timeout=10)
        return r.json().get("result", [])
    except:
        return []

# ========== ОТВЕТЫ ==========
RESPONSES = [
    (["привет", "здравствуй", "добрый", "ку", "хай", "хелло", "прив", "здарова", "салют"],
     "Привет! 👋\n\nПродаю сопровождение на 7 карту 10кк радка. Интересует? Спрашивай."),
    (["что это", "что входит", "описание", "подробнее", "расскажи", "сопровождение"],
     "Услуга: сопровождение на 7 карту, 10кк радка.\n\nПомогаю пройти/закрыть 7 карту с радкой 10кк. Всё чисто, аккуратно.\n\nЦена в лоте. Нужны детали — пиши."),
    (["цена", "стоит", "стоимость", "сколько", "дорого", "прайс", "ценник"],
     "Цена указана в лоте, она финальная.\n\nОплата через FunPay. Как оплатишь — договариваемся и я захожу."),
    (["скидка", "скидку", "дешевле", "торг", "уступи", "дороговато"],
     "Цена фикс. Но если нужно несколько карт — можем обсудить."),
    (["гарантия", "обман", "кидалово", "отзывы", "надёжно", "бан", "забанят"],
     "Работаю без банов, всё чисто. На FunPay давно, отзывы в профиле. Сделка защищена системой."),
    (["как купить", "оплата", "покупка", "способ"],
     "Жмёшь «Купить» на странице лота → оплачиваешь → я захожу и делаю."),
    (["как долго", "сроки", "когда", "скорость", "быстро"],
     "По времени — 1-2 часа. Если срочно — предупреди, ускорю."),
    (["доступ", "аккаунт", "пароль", "данные"],
     "Нужен доступ к аккаунту. Только для захода в игру. После выполнения поменяешь пароль."),
    (["7 карта", "радка", "радку", "10кк", "10 кк"],
     "Да, делаю сопровождение на 7 карту с радкой 10кк. По времени — 1-2 часа."),
    (["админ", "продавец", "вызвать", "позвать", "оператор", "живой человек", "лично"],
     "Сейчас позову продавца! ⏳\n\nОн уже получил уведомление и скоро ответит."),
    (["телеграм", "telegram", "tg", "контакт", "связь"],
     "Мой Telegram: @funpay_myboy_bot\n\nМожешь написать туда."),
    (["спасибо", "понял", "ок", "хорошо", "ладно", "договорились", "окей"],
     "Отлично! Надумаешь — жми «Купить». На связи 😊"),
    ([""], "Я онлайн! Продаю сопровождение на 7 карту 10кк радка.\n\nСпрашивай:\n— Что входит\n— Цена\n— Сроки (1-2 часа)\n— Как оплатить\n\nЕсли нужен продавец — напиши «позвать продавца»."),
]

CALL_KEYWORDS = ["админ", "продавец", "вызвать", "позвать", "оператор", "живой человек", "лично"]

def get_response(message):
    msg_lower = message.lower().strip()
    for keywords, response in RESPONSES:
        if not keywords: continue
        for kw in keywords:
            if kw in msg_lower:
                return response, (keywords == CALL_KEYWORDS)
    return RESPONSES[-1][1], False

# ========== FUNPAY ==========
class FunPayBot:
    def __init__(self):
        self.logged_in = False
        self.csrf_token = None
        self.username = "Продавец"
        self._new_session()
    
    def _new_session(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9',
        })
    
    def _find_csrf(self, text):
        patterns = [
            r'name="_csrf"[^>]*value="([^"]*)"',
            r'csrf-token[^>]*content="([^"]*)"',
            r'"_csrf"\s*:\s*"([^"]*)"',
            r'csrfToken\s*=\s*"([^"]*)"',
            r'data-csrf="([^"]*)"',
        ]
        for p in patterns:
            m = re.search(p, text)
            if m: return m.group(1)
        return None
    
    def login(self):
        try:
            self._new_session()
            r = self.session.get('https://funpay.com/', timeout=30)
            self.csrf_token = self._find_csrf(r.text)
            
            if not self.csrf_token:
                for c in self.session.cookies:
                    if 'csrf' in c.name.lower():
                        self.csrf_token = c.value
                        break
            
            if not self.csrf_token:
                logger.error("CSRF не найден")
                return False
            
            import time; time.sleep(1)
            
            r = self.session.post('https://funpay.com/account/login',
                                  data={'username': FUNPAY_EMAIL, 'password': FUNPAY_PASSWORD, '_csrf': self.csrf_token},
                                  headers={'X-Requested-With': 'XMLHttpRequest', 'Referer': 'https://funpay.com/',
                                           'Content-Type': 'application/x-www-form-urlencoded'}, timeout=30)
            
            if r.status_code == 200 and 'error' not in r.text.lower():
                time.sleep(1)
                check = self.session.get('https://funpay.com/account/', timeout=30)
                if 'logout' in check.text.lower() or 'выход' in check.text.lower():
                    self.logged_in = True
                    nick = re.search(r'data-user-name="([^"]*)"', check.text)
                    self.username = nick.group(1) if nick else FUNPAY_EMAIL.split('@')[0]
                    logger.info(f"✅ Вход: {self.username}")
                    return True
            return False
        except Exception as e:
            logger.error(f"❌ {e}")
            return False
    
    def check_messages(self):
        if not self.logged_in and not self.login():
            return []
        try:
            r = self.session.get('https://funpay.com/chat/', headers={'X-Requested-With': 'XMLHttpRequest'}, timeout=30)
            return self.parse(r.text) if r.status_code == 200 else []
        except:
            return []
    
    def parse(self, html):
        new = []
        for msg_id, text in re.findall(r'data-message-id="(\d+)".*?>(.*?)</div>', html, re.DOTALL):
            msg_id = msg_id.strip()
            if msg_id not in processed_messages:
                clean = re.sub(r'<[^>]+>', '', text).strip()
                clean = re.sub(r'\s+', ' ', clean)
                if clean and len(clean) > 2:
                    node_match = re.search(r'href="/chat/\?node=(\d+)"', html)
                    node_id = node_match.group(1) if node_match else msg_id
                    new.append({'id': msg_id, 'text': clean, 'sender': 'Покупатель', 'node_id': node_id})
        return new
    
    def send(self, chat_id, message):
        if not self.logged_in: self.login()
        try:
            r = self.session.post('https://funpay.com/chat/send',
                                  data={'msg': message, 'interlocutor': chat_id, '_csrf': self.csrf_token},
                                  headers={'X-Requested-With': 'XMLHttpRequest',
                                           'Referer': f'https://funpay.com/chat/?interlocutor={chat_id}'}, timeout=30)
            return r.status_code == 200
        except:
            return False

funpay_bot = FunPayBot()

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
def process_message(msg):
    with message_lock:
        if msg['id'] in processed_messages: return
        processed_messages.add(msg['id'])
        
        reply, is_call = get_response(msg['text'])
        sender = msg.get('sender', 'Покупатель')
        node_id = msg.get('node_id', msg['id'])
        chat_link = f"https://funpay.com/chat/?node={node_id}"
        
        funpay_bot.send(msg['id'], reply)
        
        if is_call:
            tg_send(f"🚨 <b>ВЫЗОВ ПРОДАВЦА!</b>\n\n👤 {sender}\n💬 {msg['text']}\n🕐 {datetime.now().strftime('%H:%M:%S')}\n\n📩 <a href='{chat_link}'>Открыть чат</a>", False)
        else:
            tg_send(f"🔔 <b>FunPay</b>\n\n👤 {sender}\n💬 {msg['text']}\n🕐 {datetime.now().strftime('%H:%M:%S')}\n\n💬 Бот: {reply[:150]}\n\n📩 <a href='{chat_link}'>Открыть чат</a>", True)

def scheduled_check():
    for m in funpay_bot.check_messages():
        Thread(target=process_message, args=(m,)).start()

# ========== ОБРАБОТКА КОМАНД ИЗ ТГ ==========
last_update_id = 0

def process_tg_commands():
    global last_update_id
    updates = tg_get_updates(last_update_id + 1)
    for upd in updates:
        last_update_id = upd["update_id"]
        msg = upd.get("message")
        if not msg: continue
        text = msg.get("text", "")
        chat_id = msg["chat"]["id"]
        
        if str(chat_id) != TELEGRAM_CHAT_ID: continue  # только ты
        
        if text == "/start":
            tg_send("🤖 <b>FunPay AutoResponder</b>\n\n"
                    "Команды:\n"
                    "/status — статистика\n"
                    "/login — перезайти в FunPay\n"
                    "/check — проверить сообщения\n"
                    "/on — включить автоответы\n"
                    "/off — выключить автоответы")
        elif text == "/status":
            tg_send(f"📊 <b>Статус</b>\n\n"
                    f"FunPay: {'✅' if funpay_bot.logged_in else '❌'} {funpay_bot.username}\n"
                    f"Отвечено: {len(processed_messages)}\n"
                    f"Время: {datetime.now().strftime('%H:%M:%S')}")
        elif text == "/login":
            tg_send("🔄 Перезаход в FunPay...")
            ok = funpay_bot.login()
            tg_send(f"✅ Вход: {funpay_bot.username}" if ok else "❌ Не удалось войти")
        elif text == "/check":
            msgs = funpay_bot.check_messages()
            for m in msgs:
                Thread(target=process_message, args=(m,)).start()
            tg_send(f"📩 Найдено: {len(msgs)}" if msgs else "📭 Новых нет")

# ========== ВЕБ (для здоровья) ==========
@app.route('/')
def index():
    return jsonify({'status': 'running', 'funpay': funpay_bot.logged_in, 'username': funpay_bot.username, 'processed': len(processed_messages)})

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    logger.info("⚡ FunPay TG Bot старт")
    funpay_bot.login()
    tg_send("🟢 <b>Бот FunPay запущен!</b>\n\nЛот: Сопровождение на 7 карту 10кк радка\n\nКоманды:\n/status — статус\n/login — перезаход\n/check — проверка\n/on /off — вкл/выкл")
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_check, 'interval', seconds=CHECK_INTERVAL)
    scheduler.add_job(process_tg_commands, 'interval', seconds=5)
    scheduler.start()
    
    app.run(host='0.0.0.0', port=PORT, debug=False)
