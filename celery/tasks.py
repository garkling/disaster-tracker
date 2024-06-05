import os
import requests

from dotenv import load_dotenv
from celery import Celery
from celery.schedules import crontab


load_dotenv()
app = Celery("tasks", track_started=True)

app.conf.beat_schedule = {
    "check_calendar_events": {
        "task": "tasks.scheduled_check",
        "schedule": crontab(minute="0", hour="*/12")
    },
    "eonet_updates": {
        "task": "tasks.eonet_updates",
        "schedule": crontab(minute="0", hour="*/12")
    },
}


@app.task(name="tasks.scheduled_check")
def check_calendar_events():
    response = requests.post(f"http://{os.environ['API_HOST']}:{os.environ['API_PORT']}/{os.environ['E_CHECK_ROUTE']}")
    print(response.json())


@app.task(name="tasks.eonet_updates")
def fetch_disaster_event_updates():

    for _ in range(5):  # retries
        response = requests.post(f"http://{os.environ['API_HOST']}:{os.environ['API_PORT']}/{os.environ['EONET_FETCH_ROUTE']}")
        if response.ok:
            break
