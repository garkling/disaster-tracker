FROM python:3.10


RUN mkdir /app
WORKDIR /app

RUN pip install celery requests python-dotenv redis

COPY . /app
