import math, datetime
from datetime import datetime, timedelta


def calc_pct(val1, val2, val3, val4) -> float:
    """subfunction for calc pct"""
    # pct = (val1 - val2) / (val3 - val4)
    pct = math.pow((val1 - val2) / (val3 - val4), 2)
    return pct


def calc_pct1(val1, val2, val3, val4) -> float:
    """subfunction for calc pct"""
    pct = (val1 - val2) / (val3 - val4)
    # print(pct)
    return pct


min_brightness = 0
max_brightness = 100
delta_brightness = max_brightness - min_brightness


now = datetime.strptime("25/04/28 07:05:24", "%d/%m/%y %H:%M:%S")
dawn = datetime.strptime("25/04/28 01:52:00", "%d/%m/%y %H:%M:%S")
sunrise = datetime.strptime("25/04/28 05:43:00", "%d/%m/%y %H:%M:%S")
print(now)
morning_pct = calc_pct1(
    now,
    dawn,
    sunrise,
    dawn,
)

ts_morning = (delta_brightness * morning_pct) + min_brightness
print("linear: " + str(ts_morning) + " %")

morning_pct = calc_pct(
    now,
    dawn,
    sunrise,
    dawn,
)

ts_morning = (delta_brightness * morning_pct) + min_brightness
# ts_morning = (delta_brightness * math.sqrt(morning_pct)) + min_brightness
print("Func: " + str(ts_morning) + " %")


##########


now1 = timedelta(hours=10, minutes=00, seconds=00) + now
sunset = datetime.strptime("25/04/28 17:04:24", "%d/%m/%y %H:%M:%S")
dusk = datetime.strptime("25/04/28 20:57:00", "%d/%m/%y %H:%M:%S")

print(now1)
evening_pct = calc_pct1(
    dusk,
    now1,
    sunset,
    dusk,
)

ts_evening = (delta_brightness * evening_pct) + min_brightness
# ts_evening = 100 - ((delta_brightness * evening_pct) + min_brightness)
print("linear: " + str(ts_evening) + " %")

evening_pct = calc_pct(
    dusk,
    now1,
    sunset,
    dusk,
)

ts_evening = (delta_brightness * evening_pct) + min_brightness
# ts_evening = (delta_brightness * math.sqrt(morning_pct)) + min_brightness
# ts_evening = 100 - ((delta_brightness * math.pow(evening_pct, 2)) + min_brightness)
print("Func: " + str(ts_evening) + " %")
print("Func: " + str(ts_evening) + " %")
