import datetime
d1 = datetime.datetime(2022, 4, 24, 23, 42, 40, 242692, tzinfo=datetime.timezone.utc)
d2 = datetime.datetime(2022, 4, 23, 23, 24, 20, tzinfo=<UTC>)
print(d1>d2)