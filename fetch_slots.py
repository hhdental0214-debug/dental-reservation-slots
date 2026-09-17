from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import re

CLINICS = [
    {
        "id": "clinic001",
        "name": "名古屋みなみ歯科・矯正歯科",
        "url": "https://reservation.stransa.co.jp/70f538a9b6b451966575ca5558321581/reserve/select-frame?web-menu-id=47367"
    },
    {
        "id": "clinic002",
        "name": "エスカ歯科・矯正歯科",
        "url": "https://reservation.stransa.co.jp/ba3a0e991e36324d13bbc5829d110d05/reserve/select-frame?web-menu-id=5908"
    }
]

JST = ZoneInfo("Asia/Tokyo")
TODAY = datetime.now(JST).date()
END_DATE = TODAY + timedelta(days=14)

def parse_visible_week(page):
    body = page.locator("body").inner_text()

    # 画面上の「曜日＋日」の並びを拾う
    m = re.search(
        r"(月|火|水|木|金|土|日)\s*(\d{1,2})\s*日",
        body
    )

    if not m:
        return []

    # 列ボタンの座標から7列を取る
    buttons = page.locator("button")
    cells = []

    for i in range(buttons.count()):
        btn = buttons.nth(i)
        try:
            box = btn.bounding_box()
            text = btn.inner_text().strip()
            if (
                box
                and 300 <= box["x"] <= 1000
                and 40 <= box["height"] <= 55
                and text == ""
            ):
                cells.append({
                    "x": round(box["x"]),
                    "y": round(box["y"]),
                    "cursor": btn.evaluate("(el) => getComputedStyle(el).cursor")
                })
        except:
            pass

    return cells

def get_available_days(page):
    buttons = page.locator("button")
    result = []

    for i in range(buttons.count()):
        btn = buttons.nth(i)
        try:
            text = btn.inner_text().strip()
            if text.isdigit() and 1 <= int(text) <= 31 and not btn.is_disabled():
                result.append(int(text))
        except:
            pass

    return result

def detect_dates_from_header(page):
    body = page.locator("body").inner_text()

    # 年
    year_match = re.search(r"(\d{4})年", body)
    if not year_match:
        return []

    year = int(year_match.group(1))

    # 月
    month_match = re.search(r"(\d{1,2})\s*月", body)
    if not month_match:
        return []

    month = int(month_match.group(1))

    # 画面上に並んでいる「曜日→日」を取得
    matches = re.findall(
        r"(?:月|火|水|木|金|土|日)\s*(\d{1,2})\s*日",
        body
    )

    dates = []
    prev_day = None
    current_month = month
    current_year = year

    for d in matches[:7]:
        day = int(d)

        if prev_day is not None and day < prev_day:
            current_month += 1
            if current_month == 13:
                current_month = 1
                current_year += 1

        try:
            dt = datetime(current_year, current_month, day).date()
            dates.append(dt)
        except:
            pass

        prev_day = day

    return dates

def detect_time_rows(page):
    rows = page.evaluate("""
    () => {
        const strongs = [...document.querySelectorAll('strong')];

        return strongs
            .map(el => {
                const text = (el.textContent || '').trim();
                const r = el.getBoundingClientRect();

                return {
                    text,
                    x: Math.round(r.x),
                    y: Math.round(r.y)
                };
            })
            .filter(x => /^\\d{2}:\\d{2}$/.test(x.text));
    }
    """)

    result = {}
    for row in rows:
        result[row["y"]] = row["text"]

    return result

def get_slots_for_visible_week(page):
    dates = detect_dates_from_header(page)
    time_rows = detect_time_rows(page)

    if not dates or not time_rows:
        return {}

    # 時間セルのボタンだけ取得
    buttons = page.evaluate("""
    () => {
        return [...document.querySelectorAll('button')]
            .map(el => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);

                return {
                    x: Math.round(r.x),
                    y: Math.round(r.y),
                    w: Math.round(r.width),
                    h: Math.round(r.height),
                    cursor: s.cursor
                };
            })
            .filter(x =>
                x.w >= 80 &&
                x.w <= 100 &&
                x.h >= 40 &&
                x.h <= 55 &&
                x.x >= 300 &&
                x.x <= 1000 &&
                x.y >= 400
            );
    }
    """)

    # x座標の列位置を自動認識
    xs = sorted(set(x["x"] for x in buttons))
    if len(xs) < 7:
        return {}

    xs = xs[:7]

    # y座標と時間の対応を近似
    time_y_values = sorted(time_rows.keys())

    result = {d.isoformat(): [] for d in dates}

    for cell in buttons:
        if cell["cursor"] != "pointer":
            continue

        nearest_x = min(xs, key=lambda x: abs(x - cell["x"]))
        col_index = xs.index(nearest_x)

        if col_index >= len(dates):
            continue

        nearest_y = min(time_y_values, key=lambda y: abs(y - cell["y"]))

        if abs(nearest_y - cell["y"]) > 20:
            continue

        time_text = time_rows[nearest_y]
        date_text = dates[col_index].isoformat()

        result.setdefault(date_text, []).append(time_text)

    for date_text in result:
        result[date_text] = sorted(set(result[date_text]))

    return result

def fetch_clinic(page, clinic):
    print("\n==============================")
    print(clinic["name"])
    print("==============================")

    page.goto(clinic["url"], wait_until="networkidle", timeout=60000)

    available_days = get_available_days(page)

    if not available_days:
        return {
            "status": "ok",
            "slots": {}
        }

    # 最初にクリックできる日へ進む
    first_day = str(available_days[0])
    page.get_by_role("button", name=first_day, exact=True).click()
    page.wait_for_timeout(1200)

    all_slots = {}

    # 3週間ぶん見れば直近15日は十分
    for week_index in range(3):
        week_slots = get_slots_for_visible_week(page)

        for date_text, times in week_slots.items():
            dt = datetime.fromisoformat(date_text).date()

            if TODAY <= dt <= END_DATE:
                all_slots[date_text] = times

        if week_index < 2:
            next_btn = page.get_by_role("button", name="次へ", exact=True)

            if next_btn.count() == 0:
                break

            try:
                if next_btn.is_disabled():
                    break
            except:
                pass

            next_btn.click()
            page.wait_for_timeout(1000)

    return {
        "status": "ok",
        "slots": dict(sorted(all_slots.items()))
    }

output = {
    "generated_at": datetime.now(JST).isoformat(),
    "period": {
        "from": TODAY.isoformat(),
        "to": END_DATE.isoformat()
    },
    "clinics": {}
}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    for clinic in CLINICS:
        page = browser.new_page(
            viewport={"width": 1280, "height": 900}
        )

        try:
            result = fetch_clinic(page, clinic)

            output["clinics"][clinic["id"]] = {
                "name": clinic["name"],
                "reservation_url": clinic["url"],
                "updated_at": datetime.now(JST).isoformat(),
                **result
            }

            print("取得完了")

        except Exception as e:
            print("取得失敗:", e)

            output["clinics"][clinic["id"]] = {
                "name": clinic["name"],
                "reservation_url": clinic["url"],
                "updated_at": datetime.now(JST).isoformat(),
                "status": "error",
                "error": str(e),
                "slots": {}
            }

        finally:
            page.close()

    browser.close()

output_path = "/Users/hasegawa/Desktop/slots.json"

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print("\n==============================")
print("完了")
print("==============================")
print("出力先:", output_path)
print()

for clinic_id, clinic in output["clinics"].items():
    print(clinic_id, clinic["name"])
    print("status:", clinic["status"])

    for date_text, times in clinic["slots"].items():
        if times:
            print(" ", date_text, times)

    print()
