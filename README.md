# Craiscraper

## Description

Scan (and monitor) Craigslist for new apartments within the defined parameters.

### Features
- sends notifications (after first run) for any new apartment
- extract features like parking, gym, pool, availability and apartment size from the description
- calculate distance from a location (i.e.: your work place)
- stores everything conveniently in a sqlite database

## Requirements
- Python 3.10+

## Usage

### Docker
1. create a folder for the app and change your workdir: ```mkdir craigscraper; cd craigscraper```
1. create a notification file using the instructions below ```vi notifications.yaml```
1. prepare a ```.env``` file using the ```.env_template``` as model. Remember to uncomment the last two lines to allow data persistance
1. run docker: ```docker run --env-file ./craigscraper/.env -v ./craigscraper:/persist ghcr.io/porelli/craigscraper:main```

#### if you want to examinate the database
- ```docker run -it --rm -p 8080:8080 -v ./craigscraper:/data -e SQLITE_DATABASE=rents.db coleifer/sqlite-web```

### Local or dev
1. clone the package
1. copy ```.env_template``` to ```.env```
1. replace values in ```.env```
1. create venv: ```python3 -m venv .venv```
1. activate venv: ```source .venv/bin/activate```
1. install dependencies: ```pip3 install -r requirements.txt```
1. profit: ```scrapy crawl rent```

#### Periodic scan
- suppress the notification test in the .env file using ```SUPPRESS_TEST_NOTIFICATION='True'```
- specify the scan interval in the .env file using ```MINUTES_INTERVAL```
- run: ```python3 main.py```

## Caveats (PRs are welcome!)
- currently it works only for Vancouver, BC
- code is not very organized and does not follow all the scrapy best practices

## Notifications

By default, the script will try to send a desktop notification. A first notification is sent at the beginning as a test. Subsequent notifications are sent when new apartments appear in the search on there is a price change.

You can override the default behavior specifying your own [notification provider(s)](https://github.com/caronc/apprise/wiki) with an [apprise compatible configuration file](https://github.com/caronc/apprise/wiki/config) and using the ```-a notifications_file=<NOTIFICATION_FILE>``` CLI option or via ENV ```NOTIFICATION_FILE```.

Please note, that for notifications to work on Mac or Windows, you may need to install additional packages. If you experience any errors with this, please refer to the [apprise wiki](https://github.com/caronc/apprise/wiki).

### MacOS

```bash
brew install terminal-notifier
```

### Windows

```bash
pip install pywin32
```