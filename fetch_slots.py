from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import re

JST = ZoneInfo("Asia/Tokyo")
TODAY = datetime.now(JST).date()
END_DATE = TODAY + timedelta(days=14)

CLINICS = [
    {
        "id": "clinic001",
        "system": "stransa",
        "name": "名古屋みなみ歯科・矯正歯科",
        "url": "https://reservation.stransa.co.jp/70f538a9b6b451966575ca5558321581/reserve/select-frame?web-menu-id=47367"
    },
    {
        "id": "clinic002",
        "system": "stransa",
        "name": "エスカ歯科・矯正歯科",
        "url": "https://reservation.stransa.co.jp/ba3a0e991e36324d13bbc5829d110d05/reserve/select-frame?web-menu-id=5908"
    },
    {
        "id": "clinic003",
        "system": "genie",
        "name": "いとデンタルクリニック",
        "url": "https://reserve.dental/web/e97ee2-844/register/calendar?p1=new7",
        "menu": "矯正相談（無料）"
    }
]

def get_stransa_available_days(page):
    buttons = page.locator("button")
    result = []

    for i in range(buttons.count()):
        btn = buttons.nth(i)

        try:
            text = btn.inner_text().strip()

            if (
                text.isdigit()
                and 1 <= int(text) <= 31
                and not btn.is_disabled()
            ):
                result.append(int(text))
        except:
            pass

    return result

def stransa_detect_dates(page):
    body = page.locator("body").inner_text()

    year_match = re.search(r"(\d{4})年", body)
    month_match = re.search(r"(\d{1,2})\s*月", body)

    if not year_match or not month_match:
        return []

    year = int(year_match.group(1))
    month = int(month_match.group(1))

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
            dt = datetime(
                current_year,
                current_month,
                day
            ).date()

            dates.append(dt)
        except:
            pass

        prev_day = day

    return dates

def stransa_detect_time_rows(page):
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

def stransa_get_visible_week(page):
    dates = stransa_detect_dates(page)
    time_rows = stransa_detect_time_rows(page)

    if not dates or not time_rows:
        return {}

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

    xs = sorted(set(x["x"] for x in buttons))

    if len(xs) < 7:
        return {}

    xs = xs[:7]

    time_y_values = sorted(time_rows.keys())

    result = {
        d.isoformat(): []
        for d in dates
    }

    for cell in buttons:
        if cell["cursor"] != "pointer":
            continue

        nearest_x = min(
            xs,
            key=lambda x: abs(x - cell["x"])
        )

        col_index = xs.index(nearest_x)

        if col_index >= len(dates):
            continue

        nearest_y = min(
            time_y_values,
            key=lambda y: abs(y - cell["y"])
        )

        if abs(nearest_y - cell["y"]) > 20:
            continue

        time_text = time_rows[nearest_y]
        date_text = dates[col_index].isoformat()

        result.setdefault(
            date_text,
            []
        ).append(time_text)

    for date_text in result:
        result[date_text] = sorted(
            set(result[date_text])
        )

    return result

def fetch_stransa(page, clinic):
    page.goto(
        clinic["url"],
        wait_until="networkidle",
        timeout=60000
    )

    available_days = get_stransa_available_days(page)

    if not available_days:
        return {}

    first_day = str(available_days[0])

    page.get_by_role(
        "button",
        name=first_day,
        exact=True
    ).click()

    page.wait_for_timeout(1000)

    all_slots = {}

    for week_index in range(3):
        week_slots = stransa_get_visible_week(page)

        for date_text, times in week_slots.items():
            dt = datetime.fromisoformat(
                date_text
            ).date()

            if TODAY <= dt <= END_DATE:
                all_slots[date_text] = times

        if week_index < 2:
            next_btn = page.get_by_role(
                "button",
                name="次へ",
                exact=True
            )

            if next_btn.count() == 0:
                break

            try:
                if next_btn.is_disabled():
                    break
            except:
                pass

            next_btn.click()
            page.wait_for_timeout(900)

    return dict(
        sorted(all_slots.items())
    )

def parse_genie_day(label, base_year, base_month):
    label = label.strip()

    if "/" in label:
        month, day = map(
            int,
            label.split("/")
        )

        year = base_year

        if month < base_month:
            year += 1

        return datetime(
            year,
            month,
            day
        ).date()

    day = int(label)

    return datetime(
        base_year,
        base_month,
        day
    ).date()

def fetch_genie(page, clinic):
    page.goto(
        clinic["url"],
        wait_until="networkidle",
        timeout=60000
    )

    page.get_by_text(
        "診療予約",
        exact=True
    ).click()

    page.wait_for_timeout(700)

    first = page.get_by_text(
        "初めて",
        exact=True
    )

    if first.count():
        first.first.click()

    select = page.locator(
        "select[name='select']"
    )

    select.select_option(
        label=clinic["menu"]
    )

    page.wait_for_timeout(300)

    nexts = page.get_by_text(
        "次へ",
        exact=True
    )

    for i in range(nexts.count()):
        el = nexts.nth(i)

        if el.is_visible():
            el.click()
            break

    page.wait_for_timeout(1000)

    body_text = page.locator(
        "body"
    ).inner_text()

    month_match = re.search(
        r"(\d{4})年(\d{1,2})月",
        body_text
    )

    if not month_match:
        raise Exception(
            "ジニーの年月を取得できませんでした"
        )

    base_year = int(
        month_match.group(1)
    )

    base_month = int(
        month_match.group(2)
    )

    labels = page.locator(
        ".day.enabled-day"
    ).evaluate_all("""
        els => els.map(
            el => (el.innerText || '').trim()
        )
    """)

    result = {}

    for label in labels:
        try:
            date_obj = parse_genie_day(
                label,
                base_year,
                base_month
            )
        except:
            continue

        if date_obj < TODAY or date_obj > END_DATE:
            continue

        clicked = page.evaluate("""
        label => {
            const els = [
                ...document.querySelectorAll(
                    '.day.enabled-day'
                )
            ];

            const target = els.find(
                el =>
                    (el.innerText || '').trim()
                    === label
            );

            if (!target) return false;

            target.click();
            return true;
        }
        """, label)

        if not clicked:
            continue

        page.wait_for_timeout(400)

        times = page.locator(
            ".not-select-toggle"
        ).evaluate_all("""
            els => els
                .map(
                    el =>
                        (el.innerText || '').trim()
                )
                .filter(
                    text =>
                        /^\\d{1,2}:\\d{2}$/.test(text)
                )
        """)

        result[
            date_obj.isoformat()
        ] = sorted(set(times))

    return dict(
        sorted(result.items())
    )

output = {
    "generated_at": datetime.now(JST).isoformat(),
    "period": {
        "from": TODAY.isoformat(),
        "to": END_DATE.isoformat()
    },
    "clinics": {}
}

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True
    )

    for clinic in CLINICS:
        print("\n==============================")
        print(clinic["name"])
        print("==============================")

        page = browser.new_page(
            viewport={
                "width": 1280,
                "height": 900
            }
        )

        try:
            if clinic["system"] == "stransa":
                slots = fetch_stransa(
                    page,
                    clinic
                )

            elif clinic["system"] == "genie":
                slots = fetch_genie(
                    page,
                    clinic
                )

            else:
                raise Exception(
                    "未対応の予約システム"
                )

            output["clinics"][
                clinic["id"]
            ] = {
                "name": clinic["name"],
                "system": clinic["system"],
                "reservation_url": clinic["url"],
                "updated_at": datetime.now(
                    JST
                ).isoformat(),
                "status": "ok",
                "slots": slots
            }

            if "menu" in clinic:
                output["clinics"][
                    clinic["id"]
                ]["menu"] = clinic["menu"]

            print("取得完了")

            for date_text, times in slots.items():
                if times:
                    print(
                        date_text,
                        times
                    )

        except Exception as e:
            print(
                "取得失敗:",
                e
            )

            output["clinics"][
                clinic["id"]
            ] = {
                "name": clinic["name"],
                "system": clinic["system"],
                "reservation_url": clinic["url"],
                "updated_at": datetime.now(
                    JST
                ).isoformat(),
                "status": "error",
                "error": str(e),
                "slots": {}
            }

        finally:
            page.close()

    browser.close()

output_path = "slots.json"

with open(
    output_path,
    "w",
    encoding="utf-8"
) as f:
    json.dump(
        output,
        f,
        ensure_ascii=False,
        indent=2
    )

print("\n==============================")
print("完了")
print("==============================")
print("出力先:", output_path)
