# from astral import LocationInfo

# city_name = 'London'
# a = Astral()
# a.solar_depression = 'civil'
# city = a[city_name]
# now = datetime.datetime.utcnow().replace(tzinfo=utc)
# sun = city.sun(date=datetime.datetime.now(), local=True)

# if now < sun['dawn']:
#     #night
#     return self.getNightBackground()
# elif now < sun['sunrise']:
#     #sunrise
#     return self.getSunSetRiseBackground()
# elif now < sun['sunset']:
#     #day
#     return self.getDayBackground()
# elif now < sun['dusk']:
#     #sunset
#     return self.getSunSetRiseBackground()
# else:
#     #night
#     return self.getNightBackground()


import astral
import datetime
from astral.sun import sun, Depression, SunDirection, noon, midnight
import pytz

dt = datetime.datetime.utcnow()
timenow = pytz.timezone('Europe/Berlin').localize(dt)


observer = astral.Observer(49.64, 8.45, 95)

# now = datetime.datetime.utcnow().replace(tzinfo=utc)
#current_utc = datetime.datetime.utcnow()
print(timenow)
# print(datetime.datetime(current_utc))
s = sun(observer, date=timenow, dawn_dusk_depression=Depression.NAUTICAL)
#print(str(SunDirection.RISING))
#s.Depression = "nautical"
print(noon(observer, timenow))
print(midnight(observer, timenow))



print(
    (
        f'Dawn:    {s["dawn"]}\n'
        f'Sunrise: {s["sunrise"]}\n'
        f'Noon:    {s["noon"]}\n'
        f'Sunset:  {s["sunset"]}\n'
        f'Dusk:    {s["dusk"]}\n'
    )
)






# Dawn:    2009-04-22 04:13:04.923309+00:00
# Sunrise: 2009-04-22 04:50:16.515411+00:00
# Noon:    2009-04-22 11:59:02+00:00
# Sunset:  2009-04-22 19:08:41.215821+00:00
# Dusk:    2009-04-22 19:46:06.362457+00:00
