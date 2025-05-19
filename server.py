from flask import Flask, request, jsonify
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import re
import traceback
import logging
import datetime
import atexit
import json
from time import sleep
from threading import Thread

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

driver = None  # Глобальна змінна браузера


def init_browser():
    """
    Ініціалізація браузера в headless режимі для швидшого виконання.
    """
    global driver
    options = webdriver.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--headless')  # Використовуємо headless-режим
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-notifications')

    driver = webdriver.Chrome(options=options)
    driver.get("https://desk.nuwm.edu.ua/cgi-bin/timetable.cgi")
    logging.info("🚀 Браузер ініціалізовано і відкрито сайт")


def update_schedule_page(group, date, retries=3, delay=5):
    """
    Оновлює сторінку розкладу для заданої групи і дати з повторними спробами.
    """
    global driver
    for attempt in range(retries):
        try:
            logging.info(f"🔄 Спроба {attempt + 1} завантажити сторінку...")
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.NAME, "group")))
            group_input = driver.find_element(By.NAME, "group")
            group_input.clear()
            group_input.send_keys(group)

            sdate_input = driver.find_element(By.NAME, "sdate")
            sdate_input.clear()
            sdate_input.send_keys(date)

            edate_input = driver.find_element(By.NAME, "edate")
            edate_input.clear()
            edate_input.send_keys(date)

            driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CLASS_NAME, "col-md-6"))
            )

            logging.info("✅ Сторінка з розкладом успішно оновлена")
            return driver.page_source

        except Exception as e:
            logging.warning(f"⚠️ Помилка при завантаженні сторінки: {e}")
            if attempt < retries - 1:
                logging.info(f"🔄 Повтор через {delay} секунд...")
                sleep(delay)
            else:
                logging.error("❌ Всі спроби завантаження сторінки невдалі")
                return None


def parse_schedule_html(html):
    """
    Парсинг HTML сторінки для отримання розкладу.
    """
    try:
        soup = BeautifulSoup(html, 'html.parser')
        schedule_data = []

        # Отримуємо блоки з розкладом
        day_blocks = soup.find_all('div', class_='col-md-6')
        for day_block in day_blocks:
            date_element = day_block.find('h4')
            if not date_element:
                continue
            date_text = date_element.text.split()[0]

            table = day_block.find('table', class_='table')
            if not table:
                continue

            rows = table.find('tbody').find_all('tr')
            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 3:
                    continue

                lesson_number = cols[0].text.strip()
                time = cols[1].text.replace('\n', '-').strip()
                details = cols[2].decode_contents().strip()

                # Очищуємо теги HTML і <br>
                details_cleaned = BeautifulSoup(details, 'html.parser').get_text(separator='\n').strip()

                # Ініціалізація змінних
                teacher = ''
                room = ''
                group = 'Вся група'  # Значення за замовчуванням
                subject = ''
                lesson_type = ''

                details_parts = [part.strip() for part in details_cleaned.split('\n') if part.strip()]
                if not details_parts:
                    continue

                # Назва предмета завжди остання
                subject_with_type = details_parts.pop(-1)
                subject_match = re.match(r'(.+?)\s*\((.+?)\)', subject_with_type)
                if subject_match:
                    subject = subject_match.group(1).strip()
                    lesson_type = subject_match.group(2).strip()
                else:
                    subject = subject_with_type.strip()

                # Обробляємо інші частини
                for part in details_parts:
                    if re.match(r'^\d{2,4}$', part):  # Аудиторія
                        room = part
                    elif re.search(r'(викладач|доцент|професор|асистент|ст\. викладач|зав\. кафедрою)', part, re.IGNORECASE):  # Викладач
                        teacher = part
                    elif re.match(r'^(Потік|Група|Збірна група)', part):  # Група
                        group = part

                schedule_data.append({
                    "date": date_text,
                    "lesson_number": lesson_number,
                    "time": time,
                    "room": room,
                    "teacher": teacher,
                    "group": group,
                    "subject": subject,
                    "lesson_type": lesson_type
                })

        logging.info(f"📦 Знайдено {len(schedule_data)} пар(а)")
        return schedule_data

    except Exception as e:
        logging.error("❌ Помилка при парсингу HTML:")
        logging.error(traceback.format_exc())
        return []


@atexit.register
def shutdown():
    """
    Закриття браузера при завершенні роботи.
    """
    global driver
    if driver:
        driver.quit()
        logging.info("🛑 Браузер закрито")


@app.route('/', methods=['POST'])
def get_schedule():
    """
    Обробка HTTP POST-запиту для отримання розкладу.
    """
    group = request.form.get("group")
    sdate = request.form.get("sdate")
    edate = request.form.get("edate")

    logging.info(f"🔍 Запит: група={group}, з={sdate}, по={edate}")

    if not group or not sdate or not edate:
        return jsonify({"error": "Не вказано групу або дату"}), 400

    try:
        datetime.datetime.strptime(sdate, "%d.%m.%Y")
        datetime.datetime.strptime(edate, "%d.%m.%Y")
    except ValueError:
        return jsonify({"error": "Некоректний формат дати"}), 400

    html = update_schedule_page(group, sdate)
    if not html:
        return jsonify({"error": "Не вдалося отримати розклад"}), 500

    schedule = parse_schedule_html(html)
    if not schedule:
        logging.warning("⚠️ Розклад не знайдено або пустий.")
        return jsonify({"message": "Розклад відсутній"}), 200

    logging.info(f"📋 Результат парсингу: {json.dumps(schedule, indent=4, ensure_ascii=False)}")
    return jsonify(schedule), 200


if __name__ == '__main__':
    init_browser()  # Ініціалізуємо браузер до старту Flask
    app.run(host='0.0.0.0', port=5050, debug=True)
