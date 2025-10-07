docker build -t tonusd-bot .
docker run -e BOT_TOKEN=8443224291:AAGBoCQEt7nqgbqDlfOB_x66z4Gm0yedB0U -e DB_PATH=/data/subs.db -v $(pwd)/data:/data tonusd-bot