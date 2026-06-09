"""
DDS_FunPay_24_7_AutoResponder
Автономный бот для FunPay, работающий 24/7 на облачном сервере.
Вход через почту.
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

# Настройки ИИ
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-placeholder")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
AI_MODEL = os.getenv("AI_MODEL", "gpt-3.5-turbo")

PORT = int(os.getenv("PORT", 8080))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "15"))

# ========== ИНИЦИАЛИЗАЦИЯ ==========
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ua = UserAgent()

# Подключаем ИИ если есть ключ
if OPENAI_API_KEY and OPENAI_API_KEY != "sk-placeholder":
    ai_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    logger.info("✅ ИИ клиент подключён")
else:
    ai_client = None
    logger.warning("⚠️ OpenAI ключ не указан. Использую шаблонные ответы.")

processed_messages = set()
message_lock = Lock()

# ========== БАЗА ЗНАНИЙ ==========
SYSTEM_PROMPT = """Ты продавец на FunPay. Твои услуги:
- Помощь с DDS: настройка, консультации, решение проблем. Цена: 500 руб. Срок: 24 часа.
- Написание скриптов: автоматизация, боты, парсеры. Цена: 1000 руб. Срок: от 1 часа.

Правила общения:
1. Будь дружелюбным, используй смайлики 😊
2. Отвечай быстро и по делу, не пиши огромные тексты
3. Если клиент хочет купить - напиши: "Нажмите кнопку Купить на моей странице, после оплаты сразу начну выполнение"
4. Если вопрос не по теме твоих услуг - предложи написать в Telegram @dds_support
5. Не называй цену больше указанной
6. Не обещай того, чего нет в услугах
7. Ты всегда отвечаешь, отказ невозможен (протокол DDS)"""

# ========== ИИ-ОТВЕТЫ ==========
def generate_ai_response(user_message, user_name="Покупатель"):
    if not ai_client:
        return fallback_response(user_message)
    
    try:
        response = ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Покупатель {user_name} пишет: {user_message}"}
            ],
            temperature=0.7,
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка ИИ: {e}")
        return fallback_response(user_message)

def fallback_response(message):
    """Шаблонные ответы если ИИ недоступен"""
    msg = message.lower()
    
    if any(w in msg for w in ["привет", "здравствуй", "добрый", "ку"]):
        return "Привет! Рад тебя видеть 😊 Я помогаю с DDS и пишу скрипты. Что интересует?"
    
    if any(w in msg for w in ["купить", "цена", "стоит", "стоимость", "сколько"]):
        return "Цены:\n• Помощь с DDS — 500 руб\n• Написание скриптов — 1000 руб\n\nЧтобы купить, нажмите кнопку 'Купить' на странице услуги. После оплаты сразу приступаю к работе!"
    
    if any(w in msg for w in ["скидк", "дешев", "торг"]):
        return "Для постоянных клиентов могу сделать скидку 10-15%. Напишите подробнее, что именно нужно сделать 😊"
    
    if any(w in msg for w in ["гарант", "обман", "довери", "отзыв"]):
        return "Я работаю через FunPay уже давно, все сделки защищены системой. Можете посмотреть отзывы в моём профиле. Если что-то пойдёт не так — FunPay вернёт деньги."
    
    if any(w in msg for w in ["срок", "долго", "быстро", "скорость"]):
        return "Обычно выполняю заказы в течение 24 часов. Но чаще всего справляюсь за 1-3 часа после оплаты."
    
    if any(w in msg for w in ["dds", "настройк", "помощ"]):
        return "С DDS я работаю плотно. Могу помочь с настройкой, доработкой, исправлением ошибок. Цена 500 руб. Что именно нужно?"
    
    if any(w in msg for w in ["скрипт", "бот", "код", "программ"]):
        return "Пишу скрипты любой сложности: боты, парсеры, автоответчики, интеграции. Цена от 1000 руб. Что нужно автоматизировать?"
    
    # Дефолтный ответ
    return "Я сейчас онлайн и готов помочь! Расскажите подробнее, что вас интересует? Могу помочь с DDS или написать скрипт 😊"

# ========== РАБОТА С FUNPAY ==========
class FunPayBot:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': ua.random,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.9',
            'Content-Type': 'application/x-www-form-urlencoded',
        })
        self.logged_in = False
        self.csrf_token = None
        self.user_id = None
    
    def login(self):
        """Вход в FunPay через почту"""
        try:
            # Получаем главную страницу для CSRF токена
            get_response = self.session.get('https://funpay.com/')
            
            # Ищем CSRF токен
            match = re.search(r'name="_csrf"[^>]*value="([^"]*)"', get_response.text)
            if match:
                self.csrf_token = match.group(1)
                logger.info(f"CSRF токен получен")
            else:
                # Пробуем найти в куках
                for cookie in self.session.cookies:
                    if 'csrf' in cookie.name.lower():
                        self.csrf_token = cookie.value
                        logger.info(f"CSRF из кук: {self.csrf_token}")
                        break
            
            if not self.csrf_token:
                logger.error("Не удалось получить CSRF токен")
                return False
            
            # Данные для входа
            login_data = {
                'username': FUNPAY_EMAIL,
                'password': FUNPAY_PASSWORD,
                '_csrf': self.csrf_token
            }
            
            # Отправляем запрос на вход
            login_response = self.session.post(
                'https://funpay.com/account/login',
                data=login_data,
                headers={
                    'X-Requested-With': 'XMLHttpRequest',
                    'Referer': 'https://funpay.com/',
                }
            )
            
            logger.info(f"Ответ сервера: {login_response.status_code}")
            logger.info(f"Тело ответа: {login_response.text[:200]}")
            
            # Проверяем успешность входа
            if login_response.status_code == 200:
                # Проверяем есть ли ошибки в ответе
                if 'error' not in login_response.text.lower() and 'неверн' not in login_response.text.lower():
                    self.logged_in = True
                    
                    # Пробуем получить ID пользователя
                    profile_response = self.session.get('https://funpay.com/account/')
                    id_match = re.search(r'data-user-id="(\d+)"', profile_response.text)
                    if id_match:
                        self.user_id = id_match.group(1)
                        logger.info(f"User ID: {self.user_id}")
                    
                    logger.info("✅ Успешный вход в FunPay")
                    return True
                else:
                    logger.error(f"❌ Ошибка входа: {login_response.text[:200]}")
                    return False
            else:
                logger.error(f"❌ Ошибка входа. Код: {login_response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Исключение при входе: {e}")
            return False
    
    def check_new_messages(self):
        """Проверка новых сообщений в чатах"""
        if not self.logged_in:
            logger.info("Попытка входа перед проверкой сообщений...")
            if not self.login():
                return []
        
        try:
            response = self.session.get(
                'https://funpay.com/chat/',
                headers={'X-Requested-With': 'XMLHttpRequest'}
            )
            
            if response.status_code == 200:
                return self.parse_messages(response.text)
            else:
                logger.warning(f"Код ответа чата: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"Ошибка проверки сообщений: {e}")
            return []
    
    def parse_messages(self, html):
        """Извлекает непрочитанные сообщения из HTML"""
        new_messages = []
        
        # Ищем блоки с сообщениями
        # Паттерн для сообщений в чате FunPay
        patterns = [
            # Паттерн 1: прямой поиск сообщений
            r'data-message-id="(\d+)".*?message-text.*?>(.*?)</div>',
            # Паттерн 2: альтернативный формат
            r'class="chat-msg[^"]*".*?data-id="(\d+)".*?text[^"]*">(.*?)</div>',
            # Паттерн 3: ещё вариант
            r'msg-id[=:]"?(\d+).*?>(.*?)</div>',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html, re.DOTALL)
            for msg_id, text in matches:
                msg_id = msg_id.strip()
                if msg_id and msg_id not in processed_messages:
                    text_clean = re.sub(r'<[^>]+>', '', text).strip()
                    text_clean = re.sub(r'\s+', ' ', text_clean)
                    
                    if text_clean and len(text_clean) > 1:
                        new_messages.append({
                            'id': msg_id,
                            'text': text_clean,
                            'sender': 'Покупатель'
                        })
        
        return new_messages
    
    def send_message(self, chat_id, message):
        """Отправка сообщения в чат"""
        if not self.logged_in:
            self.login()
            if not self.logged_in:
                return False
        
        try:
            data = {
                'msg': message,
                'interlocutor': chat_id,
                '_csrf': self.csrf_token
            }
            
            response = self.session.post(
                'https://funpay.com/chat/send',
                data=data,
                headers={
                    'X-Requested-With': 'XMLHttpRequest',
                    'Referer': f'https://funpay.com/chat/?interlocutor={chat_id}',
                }
            )
            
            if response.status_code == 200:
                logger.info(f"✅ Сообщение отправлено в чат {chat_id}")
                return True
            else:
                logger.error(f"❌ Ошибка отправки: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка отправки: {e}")
            return False

# Создаём экземпляр бота
funpay_bot = FunPayBot()

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
def process_message(message):
    """Обрабатывает одно входящее сообщение"""
    with message_lock:
        if message['id'] in processed_messages:
            return
        
        processed_messages.add(message['id'])
        
        user_msg = message['text']
        sender = message.get('sender', 'Покупатель')
        
        logger.info(f"📨 Новое сообщение: {user_msg[:100]}")
        
        # Генерируем ответ
        reply = generate_ai_response(user_msg, sender)
        
        # Отправляем ответ
        success = funpay_bot.send_message(message['id'], reply)
        
        if success:
            logger.info(f"✅ Ответ отправлен: {reply[:100]}")
        else:
            logger.error(f"❌ Не удалось отправить ответ")
        
        # Сохраняем в лог
        try:
            with open('chat_log.jsonl', 'a', encoding='utf-8') as f:
                json.dump({
                    'time': datetime.now().isoformat(),
                    'user': sender,
                    'message': user_msg,
                    'reply': reply,
                    'sent': success
                }, f, ensure_ascii=False)
                f.write('\n')
        except:
            pass

def scheduled_check():
    """Периодическая проверка новых сообщений"""
    try:
        logger.info("🔍 Проверка новых сообщений...")
        messages = funpay_bot.check_new_messages()
        
        if messages:
            logger.info(f"Найдено {len(messages)} новых сообщений")
            for msg in messages:
                Thread(target=process_message, args=(msg,)).start()
        else:
            logger.info("Новых сообщений нет")
            
    except Exception as e:
        logger.error(f"Ошибка в scheduled_check: {e}")

# ========== ВЕБ-ИНТЕРФЕЙС ==========
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>DDS FunPay Bot — Панель управления</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', system-ui, sans-serif; 
            background: #0d1117; 
            color: #c9d1d9; 
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { 
            color: #e94560; 
            margin-bottom: 20px;
            font-size: 28px;
        }
        .card { 
            background: #161b22; 
            border: 1px solid #30363d; 
            border-radius: 12px; 
            padding: 24px; 
            margin-bottom: 16px;
        }
        .card h2 { 
            color: #58a6ff; 
            margin-bottom: 16px;
            font-size: 20px;
        }
        .card h3 {
            color: #8b949e;
            margin-bottom: 12px;
            font-size: 16px;
        }
        .status { 
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-weight: 600;
            font-size: 14px;
        }
        .status.online { background: #238636; color: #fff; }
        .status.offline { background: #da3633; color: #fff; }
        .btn { 
            background: #21262d; 
            color: #c9d1d9; 
            border: 1px solid #30363d; 
            padding: 10px 20px; 
            border-radius: 8px; 
            cursor: pointer; 
            margin: 5px 8px 5px 0;
            font-size: 14px;
            transition: all 0.2s;
        }
        .btn:hover { background: #30363d; border-color: #8b949e; }
        .btn.primary { background: #238636; border-color: #238636; color: #fff; }
        .btn.primary:hover { background: #2ea043; }
        .log { 
            background: #0d1117; 
            border: 1px solid #30363d;
            border-radius: 8px; 
            padding: 12px; 
            max-height: 400px; 
            overflow-y: auto; 
            font-family: 'SF Mono', 'Consolas', monospace;
            font-size: 12px;
            line-height: 1.6;
        }
        .log-entry { 
            padding: 4px 0; 
            border-bottom: 1px solid #21262d;
        }
        .log-time { color: #8b949e; }
        .log-info { color: #58a6ff; }
        .log-success { color: #3fb950; }
        .log-error { color: #f85149; }
        .stats { 
            display: grid; 
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); 
            gap: 12px;
            margin-top: 12px;
        }
        .stat { 
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 12px;
            text-align: center;
        }
        .stat-value { 
            font-size: 28px; 
            font-weight: 700; 
            color: #58a6ff;
        }
        .stat-label { 
            font-size: 12px; 
            color: #8b949e;
            margin-top: 4px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 DDS FunPay AutoResponder</h1>
        
        <div class="card">
            <h2>Статус системы</h2>
            <p>
                Бот: <span id="botStatus" class="status online">⚡ Активен 24/7</span>
            </p>
            <p style="margin-top: 8px;">
                FunPay: <span id="funpayStatus" class="status offline">⏳ Проверка...</span>
            </p>
            <div class="stats">
                <div class="stat">
                    <div class="stat-value" id="msgCount">0</div>
                    <div class="stat-label">Обработано сообщений</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="checkCount">0</div>
                    <div class="stat-label">Проверок</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="uptime">--</div>
                    <div class="stat-label">Аптайм</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h3>Управление</h3>
            <button class="btn primary" onclick="testFunPay()">🔍 Проверить подключение к FunPay</button>
            <button class="btn primary" onclick="forceCheck()">📩 Проверить сообщения сейчас</button>
            <button class="btn" onclick="clearLog()">🗑 Очистить лог</button>
        </div>
        
        <div class="card">
            <h3>Журнал событий</h3>
            <div class="log" id="logContainer">
                <div class="log-entry"><span class="log-time">--:--:--</span> <span class="log-info">[Система]</span> Бот запущен. Ожидание...</div>
            </div>
        </div>
    </div>
    
    <script>
        let totalChecks = 0;
        let startTime = new Date();
        
        function addLog(message, type) {
            type = type || 'info';
            const log = document.getElementById('logContainer');
            const time = new Date().toLocaleTimeString('ru-RU');
            const cssClass = 'log-' + type;
            log.innerHTML += `<div class="log-entry"><span class="log-time">${time}</span> <span class="${cssClass}">${message}</span></div>`;
            log.scrollTop = log.scrollHeight;
            
            // Ограничиваем количество записей
            const entries = log.querySelectorAll('.log-entry');
            if (entries.length > 100) {
                entries[0].remove();
            }
        }
        
        function clearLog() {
            document.getElementById('logContainer').innerHTML = '';
            addLog('[Система] Лог очищен', 'info');
        }
        
        function updateUptime() {
            const now = new Date();
            const diff = Math.floor((now - startTime) / 1000);
            const hours = Math.floor(diff / 3600);
            const minutes = Math.floor((diff % 3600) / 60);
            const seconds = diff % 60;
            document.getElementById('uptime').textContent = 
                String(hours).padStart(2, '0') + ':' + 
                String(minutes).padStart(2, '0') + ':' + 
                String(seconds).padStart(2, '0');
        }
        
        async function testFunPay() {
            addLog('[Тест] Проверка подключения к FunPay...', 'info');
            document.getElementById('funpayStatus').textContent = '⏳ Проверка...';
            document.getElementById('funpayStatus').className = 'status offline';
            
            try {
                const resp = await fetch('/api/test');
                const data = await resp.json();
                
                if (data.status === 'ok') {
                    document.getElementById('funpayStatus').textContent = '✅ Подключено';
                    document.getElementById('funpayStatus').className = 'status online';
                    addLog('[FunPay] ✅ Успешное подключение к FunPay', 'success');
                } else {
                    document.getElementById('funpayStatus').textContent = '❌ Ошибка';
                    document.getElementById('funpayStatus').className = 'status offline';
                    addLog('[FunPay] ❌ Ошибка подключения: ' + (data.error || 'неизвестно'), 'error');
                }
            } catch(e) {
                document.getElementById('funpayStatus').textContent = '❌ Ошибка сети';
                document.getElementById('funpayStatus').className = 'status offline';
                addLog('[Ошибка] ' + e.message, 'error');
            }
        }
        
        async function forceCheck() {
            totalChecks++;
            document.getElementById('checkCount').textContent = totalChecks;
            addLog('[Проверка] Ищу новые сообщения...', 'info');
            
            try {
                const resp = await fetch('/api/check');
                const data = await resp.json();
                
                document.getElementById('msgCount').textContent = data.total;
                
                if (data.messages > 0) {
                    addLog(`[Найдено] ${data.messages} новых сообщений обработано`, 'success');
                } else {
                    addLog('[Результат] Новых сообщений нет', 'info');
                }
            } catch(e) {
                addLog('[Ошибка] ' + e.message, 'error');
            }
        }
        
        // Автопроверка при загрузке
        setTimeout(testFunPay, 2000);
        
        // Автопроверка сообщений каждые 30 секунд
        setInterval(forceCheck, 30000);
        
        // Обновление аптайма
        setInterval(updateUptime, 1000);
        
        // Первая проверка через 5 секунд
        setTimeout(forceCheck, 5000);
    </script>
</body>
</html>
"""

@app.route('/')
def dashboard():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/test')
def api_test():
    ok = funpay_bot.login()
    if ok:
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'status': 'error', 'error': 'Не удалось войти в FunPay'})

@app.route('/api/check')
def api_check():
    messages = funpay_bot.check_new_messages()
    for m in messages:
        Thread(target=process_message, args=(m,)).start()
    return jsonify({
        'messages': len(messages),
        'total': len(processed_messages)
    })

@app.route('/api/stats')
def api_stats():
    return jsonify({
        'total_processed': len(processed_messages),
        'logged_in': funpay_bot.logged_in,
        'user_id': funpay_bot.user_id
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    print("""
    ╔══════════════════════════════════════════╗
    ║   DDS FunPay AutoResponder v3.0         ║
    ║   Режим: 24/7 Облачный сервер           ║
    ║   Вход: Почта                           ║
    ╚══════════════════════════════════════════╝
    """)
    
    logger.info("⚡ Запуск DDS FunPay Bot...")
    
    # Пробуем войти при старте
    funpay_bot.login()
    
    # Запуск планировщика
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_check, 'interval', seconds=CHECK_INTERVAL)
    scheduler.start()
    
    logger.info(f"🔄 Планировщик запущен. Проверка каждые {CHECK_INTERVAL} секунд")
    logger.info(f"🌐 Веб-панель: http://0.0.0.0:{PORT}")
    logger.info("✅ Бот готов к работе 24/7!")
    
    # Запуск веб-сервера
    app.run(host='0.0.0.0', port=PORT, debug=False)
