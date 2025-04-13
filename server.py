from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import json
import os
from datetime import datetime

app = Flask(__name__)

# ===== Налаштування =====
CHROMEDRIVER_PATH = "/шлях/до/chromedriver"  # Оновіть шлях до chromedriver
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = 'primary'
SCHEDULE_FILE = 'schedule.json'
COOKIES_FILE = 'cookies.json'  # Для збереження cookies після авторизації

# ===== Авторизація та запуск браузера =====
def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Запускаємо браузер у фоновому режимі
    service = Service(executable_path=CHROMEDRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# ===== Завантаження або збереження cookies для авторизації =====
def load_cookies(driver):
    if os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE, 'r') as f:
            cookies = json.load(f)
        for cookie in cookies:
            driver.add_cookie(cookie)
        return True
    return False

def save_cookies(driver):
    cookies = driver.get_cookies()
    with open(COOKIES_FILE, 'w') as f:
        json.dump(cookies, f)

# ===== Парсинг розкладу з desk.nuwm.edu.ua =====
def fetch_schedule(driver, start_date, end_date):
    # Переходимо на сторінку розкладу
    driver.get("https://desk.nuwm.edu.ua/cgi-bin/timetable.cgi?n=700")

    # Перевіряємо, чи ми авторизовані
    if "Увійти через корпоративну пошту" in driver.page_source:
        return None  # Сигналізуємо про необхідність авторизації

    # Вводимо дати
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "start_date")))
    driver.find_element(By.ID, "start_date").clear()
    driver.find_element(By.ID, "start_date").send_keys(start_date)
    driver.find_element(By.ID, "end_date").clear()
    driver.find_element(By.ID, "end_date").send_keys(end_date)
    driver.find_element(By.ID, "submit_dates").click()

    # Чекаємо, поки таблиця розкладу завантажиться
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CLASS_NAME, "schedule-table")))

    # Парсимо розклад
    schedule = []
    rows = driver.find_elements(By.XPATH, "//table[@class='schedule-table']/tbody/tr")
    for row in rows:
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) >= 5:
            schedule.append({
                "date": cells[0].text.strip(),
                "day": cells[1].text.strip(),
                "time": cells[2].text.strip(),
                "subject": cells[3].text.strip(),
                "teacher": cells[4].text.strip(),
                "remote": "дистанційно" in cells[4].text.lower()
            })

    return schedule

# ===== Парсинг оцінок (заглушка, потрібно додати реальний парсинг) =====
def fetch_grades(driver):
    # TODO: Додати парсинг оцінок із desk.nuwm.edu.ua
    # Наприклад, перейти на сторінку оцінок і спарсити таблицю
    return [
        {"subject": "Математика", "grade": "5"},
        {"subject": "Програмування", "grade": "4"},
    ]

# ===== Парсинг групи студента (заглушка, потрібно додати реальний парсинг) =====
def fetch_student_group(driver):
    # TODO: Додати парсинг групи студента із desk.nuwm.edu.ua
    # Наприклад, знайти групу на сторінці профілю студента
    return "КІ-21"

# ===== Перевірка змін у розкладі =====
def are_schedules_different(old, new):
    return old != new

# ===== Синхронізація з Google Calendar =====
def sync_to_calendar(schedule, access_token):
    creds = Credentials(token=access_token, scopes=SCOPES)
    service = build('calendar', 'v3', credentials=creds)

    # Видаляємо старі події (опційно)
    events = service.events().list(calendarId=CALENDAR_ID).execute()
    for event in events.get('items', []):
        service.events().delete(calendarId=CALENDAR_ID, eventId=event['id']).execute()

    # Додаємо нові події
    for item in schedule:
        date = item['date']
        time_range = item['time'].split('-')
        start_time = f"{date}T{time_range[0]}:00"
        end_time = f"{date}T{time_range[1]}:00"

        event = {
            'summary': f"{item['subject']} - {item['teacher']}",
            'description': "Дистанційно" if item['remote'] else "Очно",
            'start': {
                'dateTime': start_time,
                'timeZone': 'Europe/Kiev',
            },
            'end': {
                'dateTime': end_time,
                'timeZone': 'Europe/Kiev',
            },
        }
        service.events().insert(calendarId=CALENDAR_ID, body=event).execute()

# ===== API-ендпоінт для розкладу =====
@app.route('/schedule', methods=['POST'])
def get_schedule():
    try:
        # Отримуємо дані із запиту
        data = request.get_json()
        access_token = data.get('access_token')
        start_date = data.get('start_date')  # Формат: "2025-03-01T00:00:00Z"
        end_date = data.get('end_date')      # Формат: "2025-05-31T23:59:59Z"

        if not access_token or not start_date or not end_date:
            return jsonify({"error": "Missing required fields"}), 400

        # Конвертуємо дати у формат, який очікує сайт (наприклад, "20.03.2025")
        start_dt = datetime.strptime(start_date, "%Y-%m-%dT%H:%M:%SZ")
        end_dt = datetime.strptime(end_date, "%Y-%m-%dT%H:%M:%SZ")
        start_date_formatted = start_dt.strftime("%d.%m.%Y")
        end_date_formatted = end_dt.strftime("%d.%m.%Y")

        # Налаштовуємо драйвер
        driver = setup_driver()

        # Завантажуємо cookies для авторизації
        driver.get("https://desk.nuwm.edu.ua/cgi-bin/classman.cgi?n=999")
        if not load_cookies(driver):
            # Якщо cookies немає, потрібно авторизуватися вручну (один раз)
            WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//a[text()='Увійти через корпоративну пошту']"))
            ).click()
            print("🔐 Увійди вручну в Google, а потім закрий вкладку з авторизацією")
            input("Натисни Enter, коли авторизація завершена...")
            save_cookies(driver)

        # Отримуємо розклад
        schedule = fetch_schedule(driver, start_date_formatted, end_date_formatted)
        if schedule is None:
            driver.quit()
            return jsonify({"error": "Authorization failed"}), 401

        # Отримуємо оцінки (заглушка)
        grades = fetch_grades(driver)

        # Отримуємо групу студента (заглушка)
        student_group = fetch_student_group(driver)

        # Зберігаємо розклад у файл і перевіряємо зміни
        if os.path.exists(SCHEDULE_FILE):
            with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
                old_schedule = json.load(f)
        else:
            old_schedule = []

        if are_schedules_different(old_schedule, schedule):
            print("🔄 Розклад оновлено. Синхронізую...")
            with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
                json.dump(schedule, f, ensure_ascii=False, indent=2)
            sync_to_calendar(schedule, access_token)
        else:
            print("✅ Розклад не змінився.")

        driver.quit()

        # Повертаємо дані у форматі JSON
        return jsonify({
            "schedule": schedule,
            "grades": grades,
            "student_group": student_group
        }), 200

    except Exception as e:
        print(f"Помилка: {e}")
        driver.quit()
        return jsonify({"error": str(e)}), 500

# ===== Запуск сервера =====
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)