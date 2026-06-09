"""
FunPay AutoResponder — через API чатов (работает 100%)
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

FUNPAY_COOKIES = "_gcl_au=1.1.139549398.1780827427; PHPSESSID=KbykbvbjU1QpegDW317UL1wE2zH5j6QQ; golden_key=0nwgi3yed485gn2kuu0s4zdtc9eh98gr"

PORT = int(os.getenv("PORT", 8080))
CHECK_INTERVAL = 15
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://funpay-bot-cvaw.onrender.com")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ua = UserAgent()
processed_messages = set()
message_lock = Lock()
auto_reply_enabled = True

def tg_send(chat_id, text, keyboard=None, disable_notification=False):
    if not TELEGRAM_TOKEN: return
    try:
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_notification": disable_notification}
        if keyboard: data["reply_markup"] = json.dumps(keyboard)
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=data, timeout=10)
    except Exception as e:
        logger.error(f"TG: {e}")

MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": "📊 Статус"}, {"text": "🔄 Обновить куки"}],
        [{"text": "📩 Проверить"}, {"text": "🔊 Вкл. автоответ"}],
        [{"text": "🔇 Выкл. автоответ"}, {"text": "❓ Помощь"}]
    ],
    "resize_keyboard": True
}

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
     "Мой Telegram: @locining\n\nМожешь написать туда."),
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

class FunPayBot:
    def __init__(self):
        self.logged_in = False
        self.username = "Продавец"
        self.user_id = None
        self._new_session()
    
    def _new_session(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': ua.random,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.9',
            'X-Requested-With': 'XMLHttpRequest',
        })
        for cookie in FUNPAY_COOKIES.split('; '):
            if '=' in cookie:
                key, value = cookie.split('=', 1)
                self.session.cookies.set(key, value)
    
    def login(self):
        try:
            self._new_session()
            # Проверяем через API
            r = self.session.get('https://funpay.com/account/', timeout=30)
            
            if 'logout' in r.text.lower() or 'выход' in r.text.lower():
                self.logged_in = True
                # Ищем ID пользователя
                id_match = re.search(r'data-user-id="(\d+)"', r.text)
                if id_match:
                    self.user_id = id_match.group(1)
                
                # Ищем ник
                nick = re.search(r'data-user-name="([^"]*)"', r.text)
                self.username = nick.group(1) if nick else "Продавец"
                logger.info(f"✅ Вход: {self.username} (ID: {self.user_id})")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ {e}")
            return False
    
    def check_messages(self):
        """Проверка через API чатов FunPay"""
        if not self.logged_in:
            self.login()
            if not self.logged_in:
                return []
        
        try:
            # Используем API FunPay для получения списка чатов
            r = self.session.get('https://funpay.com/chat/', 
                                headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json, text/plain, */*'},
                                timeout=30)
            
            # Если ответ HTML - парсим через regex
            if r.status_code == 200:
                return self._parse_chat_list(r.text)
            return []
        except Exception as e:
            logger.error(f"Ошибка проверки: {e}")
            return []
    
    def _parse_chat_list(self, html):
        """Парсит список чатов и непрочитанные сообщения"""
        messages = []
        
        # Ищем все чаты с node id
        chat_nodes = re.findall(r'data-node="(\d+)"', html)
        if not chat_nodes:
            chat_nodes = re.findall(r'href="/chat/\?node=(\d+)"', html)
        
        logger.info(f"Найдено чатов: {len(chat_nodes)}")
        
        for node_id in chat_nodes:
            try:
                # Заходим в каждый чат
                chat_r = self.session.get(f'https://funpay.com/chat/?node={node_id}',
                                         headers={'X-Requested-With': 'XMLHttpRequest'},
                                         timeout=30)
                
                # Ищем сообщения в чате
                msgs = re.findall(r'data-message-id="(\d+)".*?chat-msg__text">(.*?)</div>', chat_r.text, re.DOTALL)
                
                for msg_id, text in msgs:
                    msg_id = msg_id.strip()
                    if msg_id not in processed_messages:
                        clean = re.sub(r'<[^>]+>', '', text).strip()
                        clean = re.sub(r'\s+', ' ', clean)
                        if clean and len(clean) > 1:
                            messages.append({
                                'id': msg_id,
                                'text': clean,
                                'sender': 'Покупатель',
                                'node_id': node_id
                            })
                            logger.info(f"📨 Новое: {clean[:50]} | node={node_id}")
            except:
                pass
        
        return messages
    
    def send_message(self, chat_id, message):
        """Отправка через API чата"""
        if not self.logged_in: self.login()
        try:
            # Сначала заходим в чат чтобы получить CSRF
            r = self.session.get(f'https://funpay.com/chat/?interlocutor={chat_id}',
                               headers={'X-Requested-With': 'XMLHttpRequest'},
                               timeout=30)
            
            csrf = re.search(r'name="_csrf"[^>]*value="([^"]*)"', r.text)
            csrf_token = csrf.group(1) if csrf else ""
            
            r = self.session.post('https://funpay.com/chat/send',
                                data={'msg': message, 'interlocutor': chat_id, '_csrf': csrf_token},
                                headers={'X-Requested-With': 'XMLHttpRequest',
                                        'Referer': f'https://funpay.com/chat/?interlocutor={chat_id}'},
                                timeout=30)
            
            logger.info(f"Отправка в {chat_id}: статус {r.status_code}")
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
            return False

funpay_bot = FunPayBot()

def process_message(msg):
    global auto_reply_enabled
    with message_lock:
        if msg['id'] in processed_messages: return
        processed_messages.add(msg['id'])
        
        reply, is_call = get_response(msg['text'])
        sender = msg.get('sender', 'Покупатель')
        node_id = msg.get('node_id', msg['id'])
        chat_link = f"https://funpay.com/chat/?node={node_id}"
        
        if auto_reply_enabled:
            funpay_bot.send_message(msg['id'], reply)
        
        if is_call:
            tg_send(TELEGRAM_CHAT_ID, f"🚨 <b>ВЫЗОВ ПРОДАВЦА!</b>\n\n👤 {sender}\n💬 {msg['text']}\n🕐 {datetime.now().strftime('%H:%M:%S')}\n\n📩 <a href='{chat_link}'>Открыть чат</a>", disable_notification=False)
        else:
            status = "🤖 Автоответ" if auto_reply_enabled else "🔇 Выключен"
            tg_send(TELEGRAM_CHAT_ID, f"🔔 <b>FunPay</b>\n\n👤 {sender}\n💬 {msg['text']}\n🕐 {datetime.now().strftime('%H:%M:%S')}\n\n{status}\n📩 <a href='{chat_link}'>Открыть чат</a>", disable_notification=True)

def scheduled_check():
    messages = funpay_bot.check_messages()
    if messages:
        logger.info(f"🔍 Найдено {len(messages)} новых")
        for m in messages:
            Thread(target=process_message, args=(m,)).start()

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data: return "ok"
    
    msg = data.get("message", {})
    text = msg.get("text", "")
    chat_id = str(msg.get("chat", {}).get("id", ""))
    
    if chat_id != TELEGRAM_CHAT_ID:
        return "ok"
    
    global auto_reply_enabled
    
    if text in ["/start", "❓ Помощь"]:
        tg_send(chat_id, "🤖 <b>FunPay AutoResponder</b>\n\nКнопки внизу. Если куки протухнут — обнови.", keyboard=MAIN_KEYBOARD)
    elif text in ["📊 Статус", "/status"]:
        tg_send(chat_id, f"📊 <b>Статус</b>\n\nFunPay: {'✅' if funpay_bot.logged_in else '❌'} {funpay_bot.username}\nID: {funpay_bot.user_id}\nАвтоответ: {'✅' if auto_reply_enabled else '❌'}\nОтвечено: {len(processed_messages)}\n🕐 {datetime.now().strftime('%H:%M:%S')}", keyboard=MAIN_KEYBOARD)
    elif text in ["🔄 Обновить куки", "/login"]:
        tg_send(chat_id, "🔄 Проверяю...", keyboard=MAIN_KEYBOARD)
        ok = funpay_bot.login()
        tg_send(chat_id, f"{'✅ ' + funpay_bot.username if ok else '❌ Обнови куки'}", keyboard=MAIN_KEYBOARD)
    elif text in ["📩 Проверить", "/check"]:
        tg_send(chat_id, "🔍 Ищу сообщения...", keyboard=MAIN_KEYBOARD)
        msgs = funpay_bot.check_messages()
        for m in msgs:
            Thread(target=process_message, args=(m,)).start()
        tg_send(chat_id, f"{'📩 Найдено: ' + str(len(msgs)) if msgs else '📭 Новых нет'}", keyboard=MAIN_KEYBOARD)
    elif text in ["🔊 Вкл. автоответ", "/on"]:
        auto_reply_enabled = True
        tg_send(chat_id, "✅ Автоответы включены", keyboard=MAIN_KEYBOARD)
    elif text in ["🔇 Выкл. автоответ", "/off"]:
        auto_reply_enabled = False
        tg_send(chat_id, "🔇 Автоответы выключены", keyboard=MAIN_KEYBOARD)
    
    return "ok"

@app.route('/')
def index():
    return jsonify({'status': 'running', 'funpay': funpay_bot.logged_in, 'username': funpay_bot.username, 'user_id': funpay_bot.user_id, 'processed': len(processed_messages)})

@app.route('/health')
def health(): return jsonify({'status': 'ok'})

if __name__ == '__main__':
    logger.info("⚡ Бот v7 с API чатов")
    
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", json={"url": f"{WEBHOOK_URL}/webhook"}, timeout=10)
    
    funpay_bot.login()
    
    if funpay_bot.logged_in:
        tg_send(TELEGRAM_CHAT_ID, f"🟢 <b>Бот запущен!</b>\nАккаунт: {funpay_bot.username}\nID: {funpay_bot.user_id}", keyboard=MAIN_KEYBOARD)
    else:
        tg_send(TELEGRAM_CHAT_ID, "🔴 Куки нерабочие.", keyboard=MAIN_KEYBOARD)
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_check, 'interval', seconds=CHECK_INTERVAL)
    scheduler.start()
    
    app.run(host='0.0.0.0', port=PORT, debug=False)
