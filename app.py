"""
FunPay_AutoResponder + Telegram v3
Лот: Сопровождение на 7 карту 10кк радка
24/7, обход блокировок
"""

import os
import re
import json
import logging
import requests
from datetime import datetime
from threading import Thread, Lock
from flask import Flask, jsonify, render_template_string
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from fake_useragent import UserAgent

load_dotenv()

# ========== ДАННЫЕ ==========
FUNPAY_EMAIL = os.getenv("FUNPAY_EMAIL", "")
FUNPAY_PASSWORD = os.getenv("FUNPAY_PASSWORD", "")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

PORT = int(os.getenv("PORT", 8080))
CHECK_INTERVAL = 15

# ========== ИНИТ ==========
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ua = UserAgent()
processed_messages = set()
message_lock = Lock()

# ========== TELEGRAM ==========
def send_telegram(text, disable_notification=False):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "disable_notification": disable_notification
        }
        r = requests.post(url, data=data, timeout=10)
        if r.status_code != 200:
            logger.error(f"TG error: {r.text}")
    except Exception as e:
        logger.error(f"TG exception: {e}")

# ========== ОТВЕТЫ ==========
RESPONSES = [
    (["привет", "здравствуй", "добрый", "ку", "хай", "хелло", "прив", "здарова", "салют"],
     "Привет! 👋\n\nПродаю сопровождение на 7 карту 10кк радка. Интересует? Спрашивай."
    ),
    (["что это", "что входит", "описание", "подробнее", "расскажи", "что за услуга", "сопровождение"],
     "Услуга: сопровождение на 7 карту, 10кк радка.\n\nПомогаю пройти/закрыть 7 карту с радкой 10кк. Всё чисто, аккуратно, без багов.\n\nЦена в лоте. Нужны детали — пиши."
    ),
    (["цена", "стоит", "стоимость", "сколько", "дорого", "прайс", "ценник"],
     "Цена указана в лоте, она финальная.\n\nОплата через FunPay. Как оплатишь — договариваемся и я захожу."
    ),
    (["скидка", "скидку", "дешевле", "торг", "уступи", "дороговато"],
     "Цена фикс. Но если нужно несколько карт — можем обсудить. Напиши что именно нужно."
    ),
    (["гарантия", "гарантии", "обман", "кидалово", "отзывы", "надёжно", "доверять", "безопасно", "бан", "забанят"],
     "Работаю без банов, всё чисто. На FunPay давно, отзывы в профиле.\n\nСделка через FunPay — система защищает. Если что — деньги вернут. Но проблем не бывает."
    ),
    (["как купить", "как оплатить", "оплата", "покупка", "способ"],
     "Всё просто:\n1. Жмёшь «Купить» на странице лота\n2. Оплачиваешь\n3. Я вижу оплату, списываемся, договариваемся когда зайти\n4. Выполняю заказ"
    ),
    (["как долго", "сроки", "когда", "скорость", "быстро", "сколько времени"],
     "По времени — 1-2 часа. Обычно справляюсь быстро.\n\nЕсли срочно — предупреди, ускорю."
    ),
    (["как проходит", "как делаешь", "нужен доступ", "доступ", "аккаунт", "пароль", "данные"],
     "Для выполнения нужен доступ к аккаунту. Только для захода в игру, ничего лишнего.\n\nПосле выполнения сразу можешь поменять пароль. Репутация дороже."
    ),
    (["7 карта", "седьмая карта", "карта", "радка", "радку", "10кк", "10 кк", "рейтинг"],
     "Да, делаю сопровождение на 7 карту с радкой 10кк. Опыт есть.\n\nПо времени — 1-2 часа. Если нужно конкретное время — договоримся."
    ),
    (["админ", "продавец", "вызвать", "позвать", "оператор", "живой человек", "настоящий", "лично"],
     "Сейчас позову продавца! ⏳\n\nОн уже получил уведомление и скоро ответит лично. Ожидайте, пожалуйста."
    ),
    (["телеграм", "telegram", "tg", "дискорд", "discord", "контакт", "связь", "написать"],
     "Можешь написать в Telegram: @dds_support\n\nТам быстрее отвечаю. Но и тут на связи."
    ),
    (["спасибо", "понял", "ок", "хорошо", "ладно", "договорились", "окей"],
     "Отлично! Надумаешь — жми «Купить». На связи 😊"
    ),
    ([""],
     "Я онлайн! Продаю сопровождение на 7 карту 10кк радка.\n\nСпрашивай:\n— Что входит\n— Цена\n— Сроки (1-2 часа)\n— Как оплатить\n\nЕсли нужен продавец лично — напиши «позвать продавца».\n\nОтвечаю быстро 😊"
    ),
]

CALL_KEYWORDS = ["админ", "продавец", "вызвать", "позвать", "оператор", "живой человек", "настоящий", "лично"]

def get_response(message):
    msg_lower = message.lower().strip()
    for keywords, response in RESPONSES:
        if not keywords:
            continue
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
        self.session = None
        self._init_session()
    
    def _init_session(self):
        """Создаёт новую сессию с правильными заголовками"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        })
        # Начальные куки как у обычного браузера
        self.session.cookies.set('lang', 'ru')
        self.session.cookies.set('timezone', 'Europe/Moscow')
    
    def login(self):
        """Вход в FunPay"""
        try:
            self._init_session()
            
            # Загружаем главную (как браузер)
            logger.info("Загрузка funpay.com...")
            r = self.session.get('https://funpay.com/', timeout=30)
            logger.info(f"Главная: {r.status_code}")
            
            # Ищем CSRF
            match = re.search(r'name="_csrf"[^>]*value="([^"]*)"', r.text)
            if match:
                self.csrf_token = match.group(1)
                logger.info(f"CSRF: {self.csrf_token[:20]}...")
            else:
                logger.error("CSRF не найден")
                # Попробуем из кук
                for c in self.session.cookies:
                    if 'csrf' in c.name.lower():
                        self.csrf_token = c.value
                        logger.info(f"CSRF из кук: {self.csrf_token[:20]}...")
                        break
            
            if not self.csrf_token:
                return False
            
            # Пауза как человек
            import time
            time.sleep(1)
            
            # Логинимся
            login_url = 'https://funpay.com/account/login'
            login_data = {
                'username': FUNPAY_EMAIL,
                'password': FUNPAY_PASSWORD,
                '_csrf': self.csrf_token
            }
            
            login_headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': 'https://funpay.com/',
                'Origin': 'https://funpay.com',
            }
            
            logger.info(f"Отправка логина...")
            r = self.session.post(login_url, data=login_data, headers=login_headers, timeout=30)
            logger.info(f"Ответ логина: {r.status_code}")
            logger.info(f"Тело: {r.text[:300]}")
            
            if r.status_code == 200:
                # Проверяем ответ
                text_lower = r.text.lower()
                if 'error' in text_lower or 'неверн' in text_lower or 'неправиль' in text_lower:
                    logger.error(f"Ошибка входа: {r.text[:300]}")
                    return False
                
                # Проверяем что реально залогинились
                time.sleep(1)
                check_r = self.session.get('https://funpay.com/account/', timeout=30)
                
                if 'logout' in check_r.text.lower() or 'выход' in check_r.text.lower():
                    self.logged_in = True
                    
                    # Ищем username
                    nick = re.search(r'data-user-name="([^"]*)"', check_r.text)
                    if nick:
                        self.username = nick.group(1)
                    else:
                        nick = re.search(r'<span[^>]*class="user-name[^"]*"[^>]*>(.*?)</span>', check_r.text)
                        if nick:
                            self.username = re.sub(r'<[^>]+>', '', nick.group(1)).strip()
                    
                    logger.info(f"✅ Вход успешен: {self.username}")
                    
                    # Уведомление в ТГ
                    send_telegram(f"✅ <b>Бот вошёл в FunPay</b>\n\nАккаунт: {self.username}\nВремя: {datetime.now().strftime('%H:%M:%S')}")
                    
                    return True
                else:
                    logger.error(f"Не залогинились. Ответ: {check_r.text[:300]}")
                    return False
            else:
                logger.error(f"Код ответа: {r.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Исключение: {e}")
            import traceback
            logger.error(traceback.format_exc())
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
        msg_pattern = r'data-message-id="(\d+)".*?chat-msg__sender[^>]*>(.*?)</a>.*?chat-msg__text">(.*?)</div>'
        matches = re.findall(msg_pattern, html, re.DOTALL)
        
        if not matches:
            msg_pattern = r'data-message-id="(\d+)".*?>(.*?)</div>'
            for msg_id, text in re.findall(msg_pattern, html, re.DOTALL):
                msg_id = msg_id.strip()
                if msg_id not in processed_messages:
                    clean = re.sub(r'<[^>]+>', '', text).strip()
                    clean = re.sub(r'\s+', ' ', clean)
                    if clean and len(clean) > 2:
                        node_match = re.search(r'href="/chat/\?node=(\d+)"', html)
                        node_id = node_match.group(1) if node_match else msg_id
                        new.append({'id': msg_id, 'text': clean, 'sender': 'Покупатель', 'node_id': node_id})
        else:
            for msg_id, sender, text in matches:
                msg_id = msg_id.strip()
                if msg_id not in processed_messages:
                    sender_clean = re.sub(r'<[^>]+>', '', sender).strip()
                    text_clean = re.sub(r'<[^>]+>', '', text).strip()
                    text_clean = re.sub(r'\s+', ' ', text_clean)
                    if text_clean and len(text_clean) > 2:
                        node_match = re.search(r'href="/chat/\?node=(\d+)"', html)
                        node_id = node_match.group(1) if node_match else msg_id
                        new.append({'id': msg_id, 'text': text_clean, 'sender': sender_clean, 'node_id': node_id})
        return new
    
    def send(self, chat_id, message):
        if not self.logged_in:
            self.login()
        try:
            data = {'msg': message, 'interlocutor': chat_id, '_csrf': self.csrf_token}
            r = self.session.post('https://funpay.com/chat/send', data=data,
                                  headers={'X-Requested-With': 'XMLHttpRequest',
                                           'Referer': f'https://funpay.com/chat/?interlocutor={chat_id}'},
                                  timeout=30)
            return r.status_code == 200
        except:
            return False

funpay_bot = FunPayBot()

# ========== ОБРАБОТКА ==========
def process_message(msg):
    with message_lock:
        if msg['id'] in processed_messages:
            return
        processed_messages.add(msg['id'])
        
        reply, is_call = get_response(msg['text'])
        sender = msg.get('sender', 'Покупатель')
        node_id = msg.get('node_id', msg['id'])
        chat_link = f"https://funpay.com/chat/?node={node_id}"
        
        funpay_bot.send(msg['id'], reply)
        
        if is_call:
            tg_msg = (
                f"🚨 <b>ВЫЗОВ ПРОДАВЦА!</b>\n\n"
                f"👤 <b>{sender}</b>\n"
                f"💬 {msg['text']}\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
                f"📩 <a href='{chat_link}'>Открыть чат с покупателем</a>"
            )
            send_telegram(tg_msg, disable_notification=False)
        else:
            tg_msg = (
                f"🔔 <b>Новое сообщение на FunPay</b>\n\n"
                f"👤 <b>{sender}</b>\n"
                f"💬 {msg['text']}\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
                f"💬 <i>Ответ бота:</i> {reply[:150]}{'...' if len(reply) > 150 else ''}\n\n"
                f"📩 <a href='{chat_link}'>Открыть чат с покупателем</a>"
            )
            send_telegram(tg_msg, disable_notification=True)
        
        logger.info(f"📨 {sender}: {msg['text'][:50]} → {reply[:50]} | node={node_id}")

def scheduled_check():
    messages = funpay_bot.check_messages()
    if messages:
        logger.info(f"🔍 {len(messages)} новых")
        for m in messages:
            Thread(target=process_message, args=(m,)).start()

# ========== ВЕБ ==========
HTML = """
<!DOCTYPE html><html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FunPay Bot</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}
.container{max-width:800px;margin:0 auto}
h1{color:#e94560;margin-bottom:20px}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:24px;margin-bottom:16px}
h2{color:#58a6ff;margin-bottom:12px}
.status{display:inline-block;padding:4px 12px;border-radius:20px;font-weight:600;font-size:14px}
.online{background:#238636;color:#fff}
.offline{background:#da3633;color:#fff}
.btn{background:#21262d;color:#c9d1d9;border:1px solid #30363d;padding:10px 20px;border-radius:8px;cursor:pointer;margin:5px 8px 5px 0;font-size:14px}
.btn:hover{background:#30363d}
.btn.green{background:#238636;border-color:#238636;color:#fff}
.btn.green:hover{background:#2ea043}
.btn.red{background:#da3633;border-color:#da3633;color:#fff}
.log{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:12px;max-height:400px;overflow-y:auto;font-family:monospace;font-size:12px;line-height:1.6}
.log div{padding:4px 0;border-bottom:1px solid #21262d}
.time{color:#8b949e}.info{color:#58a6ff}.ok{color:#3fb950}.err{color:#f85149}.warn{color:#d2991d}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}
.stat{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:12px;text-align:center}
.stat-val{font-size:28px;font-weight:700;color:#58a6ff}
.stat-lbl{font-size:12px;color:#8b949e;margin-top:4px}
</style></head><body>
<div class="container">
<h1>🤖 FunPay AutoResponder</h1>
<div class="card">
<h2>Сопровождение на 7 карту 10кк радка</h2>
<p>Бот: <span class="status online">⚡ 24/7</span></p>
<p style="margin-top:8px">FunPay: <span id="fpStatus" class="status offline">⏳</span></p>
<p style="margin-top:8px">Telegram: <span id="tgStatus" class="status offline">⏳</span></p>
<div class="stats">
<div class="stat"><div class="stat-val" id="msgCount">0</div><div class="stat-lbl">Ответов</div></div>
<div class="stat"><div class="stat-val" id="checkCount">0</div><div class="stat-lbl">Проверок</div></div>
<div class="stat"><div class="stat-val" id="uptime">00:00</div><div class="stat-lbl">Аптайм</div></div>
</div>
</div>
<div class="card">
<button class="btn green" onclick="testFP()">🔍 FunPay</button>
<button class="btn green" onclick="testTG()">📱 Telegram</button>
<button class="btn green" onclick="forceCheck()">📩 Проверить</button>
<button class="btn red" onclick="testCall()">🚨 Тест вызова</button>
<button class="btn" onclick="document.getElementById('log').innerHTML=''">🗑 Лог</button>
</div>
<div class="card">
<h3>Журнал</h3>
<div class="log" id="log"><div><span class="time">--:--</span> <span class="info">Бот запущен</span></div></div>
</div>
</div>
<script>
let checks=0,start=new Date();
function addLog(m,t){t=t||'info';let d=document.getElementById('log');let time=new Date().toLocaleTimeString('ru-RU');d.innerHTML+=`<div><span class="time">${time}</span> <span class="${t}">${m}</span></div>`;d.scrollTop=d.scrollHeight}
function updateUptime(){let d=Math.floor((new Date()-start)/1000);let h=Math.floor(d/3600);let m=Math.floor((d%3600)/60);document.getElementById('uptime').textContent=String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')}
async function testFP(){addLog('Проверка входа в FunPay...','info');document.getElementById('fpStatus').textContent='⏳';document.getElementById('fpStatus').className='status offline';try{let r=await fetch('/api/test');let d=await r.json();if(d.ok){document.getElementById('fpStatus').textContent='✅ Онлайн';document.getElementById('fpStatus').className='status online';addLog('Вход выполнен: '+d.user,'ok')}else{document.getElementById('fpStatus').textContent='❌ Ошибка';document.getElementById('fpStatus').className='status offline';addLog('Ошибка: '+d.error,'err')}}catch(e){addLog(e.message,'err')}}
async function testTG(){addLog('Telegram...','info');try{let r=await fetch('/api/test_tg');let d=await r.json();if(d.ok){document.getElementById('tgStatus').textContent='✅ Онлайн';document.getElementById('tgStatus').className='status online';addLog('Работает','ok')}else{document.getElementById('tgStatus').textContent='❌ Ошибка';document.getElementById('tgStatus').className='status offline';addLog(d.error,'err')}}catch(e){addLog(e.message,'err')}}
async function testCall(){addLog('Тест вызова...','warn');try{let r=await fetch('/api/test_call');let d=await r.json();addLog(d.ok?'Отправлено':'Ошибка',d.ok?'ok':'err')}catch(e){addLog(e.message,'err')}}
async function forceCheck(){checks++;document.getElementById('checkCount').textContent=checks;try{let r=await fetch('/api/check');let d=await r.json();document.getElementById('msgCount').textContent=d.total;addLog(d.messages>0?'+'+d.messages:'Нет',d.messages>0?'ok':'info')}catch(e){addLog(e.message,'err')}}
setInterval(updateUptime,1000);setInterval(forceCheck,30000);setTimeout(testFP,3000);setTimeout(testTG,4000);
</script></body></html>
"""

@app.route('/')
def dashboard():
    return render_template_string(HTML)

@app.route('/api/test')
def api_test():
    ok = funpay_bot.login()
    return jsonify({'ok': ok, 'error': '' if ok else 'Не удалось войти', 'user': funpay_bot.username})

@app.route('/api/test_tg')
def api_test_tg():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return jsonify({'ok': False, 'error': 'Нет токена или Chat ID'})
    try:
        send_telegram("✅ <b>Тест уведомлений FunPay бота</b>\n\nВсё работает! Сообщения будут приходить с ссылкой на чат.")
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/test_call')
def api_test_call():
    try:
        send_telegram(
            f"🚨 <b>ТЕСТ ВЫЗОВА ПРОДАВЦА</b>\n\n"
            f"👤 Test_User\n💬 Позовите продавца!\n🕐 {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"📩 <a href='https://funpay.com/chat/'>Открыть чаты FunPay</a>"
        )
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/check')
def api_check():
    messages = funpay_bot.check_messages()
    for m in messages:
        Thread(target=process_message, args=(m,)).start()
    return jsonify({'messages': len(messages), 'total': len(processed_messages)})

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    logger.info("⚡ Старт")
    funpay_bot.login()
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_check, 'interval', seconds=CHECK_INTERVAL)
    scheduler.start()
    app.run(host='0.0.0.0', port=PORT, debug=False)
