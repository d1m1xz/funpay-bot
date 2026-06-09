"""
DDS_FunPay_24_7_AutoResponder
Автономный бот для FunPay, работающий 24/7 на облачном сервере.
"""

import os
import re
import json
import logging
import requests
from datetime import datetime
from threading import Thread, Lock
from flask import Flask, request, jsonify, render_template_string
from openai import OpenAI
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from fake_useragent import UserAgent

load_dotenv()

# ========== КОНФИГУРАЦИЯ ==========
FUNPAY_EMAIL = os.getenv("FUNPAY_EMAIL", "")
FUNPAY_PASSWORD = os.getenv("FUNPAY_PASSWORD", "")
FUNPAY_GOLDEN_KEY = os.getenv("FUNPAY_GOLDEN_KEY", "")

# Настройки ИИ (можно Groq бесплатно, можно OpenAI)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-placeholder")
AI_MODEL = "gpt-3.5-turbo"
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

PORT = int(os.getenv("PORT", 8080))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "15"))

# ========== ИНИЦИАЛИЗАЦИЯ ==========
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ua = UserAgent()

if OPENAI_API_KEY and OPENAI_API_KEY != "sk-placeholder":
    ai_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
else:
    ai_client = None
    logger.warning("⚠️ OpenAI ключ не указан. ИИ ответы не работают.")

processed_messages = set()
message_lock = Lock()

# ========== БАЗА ЗНАНИЙ ==========
KNOWLEDGE_BASE = """
Ты продавец на FunPay. Твои услуги:
- Помощь с DDS: настройка, консультации, решение проблем. Цена: 500 руб. Срок: 24 часа.
- Написание скриптов: автоматизация, боты, парсеры. Цена: 1000 руб. Срок: от 1 часа.

Правила общения:
1. Будь дружелюбным, используй смайлики 😊
2. Отвечай быстро и по делу
3. Если клиент хочет купить - объясни: "Нажмите кнопку Купить на моей странице"
4. Если вопрос сложный - предложи написать в Telegram @твой_ник
5. Не выдумывай лишнего
6. Отказ в ответе запрещён протоколом DDS
"""

# ========== ИИ-ОТВЕТЫ ==========
def generate_ai_response(user_message, user_name="Покупатель"):
    if not ai_client:
        return fallback_response(user_message)
    
    try:
        response = ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": KNOWLEDGE_BASE},
                {"role": "user", "content": f"{user_name}: {user_message}"}
            ],
            temperature=0.7,
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return fallback_response(user_message)

def fallback_response(message):
    msg_lower = message.lower()
    if "купить" in msg_lower or "цена" in msg_lower:
        return "Чтобы купить, нажмите кнопку 'Купить' на странице услуги. Цены: 500 руб (помощь с DDS), 1000 руб (скрипты)."
    elif "скидк" in msg_lower:
        return "Для постоянных клиентов могу сделать скидку! Напишите подробнее в Telegram @твой_ник 😊"
    elif "гарант" in msg_lower:
        return "Все сделки через FunPay защищены системой. Можете посмотреть мои отзывы в профиле!"
    else:
        return "Я сейчас онлайн! Напишите что вас интересует, и я помогу 😊"

# ========== РАБОТА С FUNPAY ==========
class FunPayBot:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': ua.random,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.9',
        })
        self.logged_in = False
        self.csrf_token = None
    
    def login(self):
        try:
            get_response = self.session.get('https://funpay.com/')
            match = re.search(r'name="_csrf"[^>]*value="([^"]*)"', get_response.text)
            if match:
                self.csrf_token = match.group(1)
            
            login_data = {
                'username': FUNPAY_PHONE,
                'password': FUNPAY_PASSWORD,
                '_csrf': self.csrf_token
            }
            
            response = self.session.post(
                'https://funpay.com/account/login',
                data=login_data,
                headers={'X-Requested-With': 'XMLHttpRequest'}
            )
            
            self.logged_in = (response.status_code == 200)
            logger.info(f"✅ Вход в FunPay: {'Успешно' if self.logged_in else 'Ошибка'}")
            return self.logged_in
        except Exception as e:
            logger.error(f"❌ Ошибка входа: {e}")
            return False
    
    def check_new_messages(self):
        if not self.logged_in:
            self.login()
        
        try:
            response = self.session.get(
                'https://funpay.com/chat/',
                headers={'X-Requested-With': 'XMLHttpRequest'}
            )
            return self.parse_messages(response.text) if response.status_code == 200 else []
        except Exception as e:
            logger.error(f"Ошибка проверки: {e}")
            return []
    
    def parse_messages(self, html):
        new_messages = []
        pattern = r'data-message-id="(\d+)".*?chat-msg__text">(.*?)</div>.*?chat-msg__sender[^>]*>(.*?)</'
        for msg_id, text, sender in re.findall(pattern, html, re.DOTALL):
            msg_id = msg_id.strip()
            if msg_id not in processed_messages:
                text_clean = re.sub(r'<[^>]+>', '', text).strip()
                sender_clean = re.sub(r'<[^>]+>', '', sender).strip()
                new_messages.append({'id': msg_id, 'text': text_clean, 'sender': sender_clean})
        return new_messages
    
    def send_message(self, chat_id, message):
        if not self.logged_in:
            self.login()
        try:
            data = {'msg': message, 'interlocutor': chat_id, '_csrf': self.csrf_token}
            response = self.session.post(
                'https://funpay.com/chat/send',
                data=data,
                headers={'X-Requested-With': 'XMLHttpRequest'}
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
            return False

funpay_bot = FunPayBot()

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
def process_message(message):
    with message_lock:
        if message['id'] in processed_messages:
            return
        processed_messages.add(message['id'])
        
        reply = generate_ai_response(message['text'], message.get('sender', 'Покупатель'))
        funpay_bot.send_message(message['id'], reply)
        logger.info(f"📨 {message['sender']}: {message['text'][:50]} → {reply[:50]}")

def scheduled_check():
    logger.info("🔍 Проверка сообщений...")
    for msg in funpay_bot.check_new_messages():
        Thread(target=process_message, args=(msg,)).start()

# ========== ВЕБ-ИНТЕРФЕЙС ==========
HTML = """
<!DOCTYPE html><html><head>
<title>DDS FunPay Bot</title>
<meta charset="utf-8">
<style>
body{font-family:sans-serif;background:#1a1a2e;color:#eee;padding:20px}
.card{background:#16213e;border-radius:10px;padding:20px;margin:10px 0}
.green{color:#0f0}.btn{background:#e94560;color:#fff;border:none;padding:10px 20px;border-radius:5px;cursor:pointer;margin:5px}
.log{background:#0a0a1a;padding:10px;max-height:300px;overflow-y:auto;font-family:monospace;font-size:12px}
</style></head><body>
<h1>🤖 DDS FunPay Bot — 24/7</h1>
<div class="card">
<h2>Статус: <span class="green">⚡ Онлайн</span></h2>
<p>Обработано сообщений: <span id="count">0</span></p>
<p>Последняя проверка: <span id="time">-</span></p>
<button class="btn" onclick="check()">📩 Проверить сейчас</button>
<button class="btn" onclick="test()">🔍 Тест соединения</button>
</div>
<div class="card"><h3>Лог</h3><div class="log" id="log">[Система] Бот запущен...</div></div>
<script>
function addLog(m){document.getElementById('log').innerHTML+='<div>'+new Date().toLocaleTimeString()+' '+m+'</div>'}
async function check(){let r=await fetch('/api/check');let d=await r.json();addLog('Проверено: '+d.messages+' сообщений');document.getElementById('count').textContent=d.total}
async function test(){let r=await fetch('/api/test');let d=await r.json();addLog('FunPay: '+d.status)}
setInterval(check,60000)
</script></body></html>
"""

@app.route('/')
def dashboard():
    return render_template_string(HTML)

@app.route('/api/check')
def api_check():
    messages = funpay_bot.check_new_messages()
    for m in messages:
        Thread(target=process_message, args=(m,)).start()
    return jsonify({'messages': len(messages), 'total': len(processed_messages)})

@app.route('/api/test')
def api_test():
    ok = funpay_bot.login()
    return jsonify({'status': '✅ Подключено' if ok else '❌ Ошибка'})

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    logger.info("⚡ DDS FunPay Bot запускается...")
    funpay_bot.login()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_check, 'interval', seconds=CHECK_INTERVAL)
    scheduler.start()
    
    logger.info(f"🔄 Проверка каждые {CHECK_INTERVAL} сек.")
    app.run(host='0.0.0.0', port=PORT, debug=False)
