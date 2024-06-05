#!/usr/bin/env bash


echo "$REDIS_USER"

sed \
  -e "s/{REDIS_USERNAME}/$REDIS_USERNAME/g" \
  -e "s/{REDIS_PASSWORD}/$REDIS_PASSWORD/" \
  /etc/redis/redis.conf \
  | redis-server -
