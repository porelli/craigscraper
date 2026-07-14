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
1. prepare a ```.env``` file using the ```.env_template``` as model. Remember to uncomment the last two lines to allow data persistence
1. OPTIONAL: change container listening port adding ```UI_PORT=...``` in the ```.env``` file
1. run docker: ```docker run --env-file ./craigscraper/.env -p 8001:8501 -v ./craigscraper:/persist ghcr.io/porelli/craigscraper:main```

#### if you want to examinate the database
- ```docker run -it --rm -p 8080:8080 -v ./craigscraper:/data -e SQLITE_DATABASE=rents.db coleifer/sqlite-web```

### Local or dev
1. clone the package: ```git clone git@github.com:porelli/craigscraper.git && cd craigscraper```
1. copy env file: ```cp .env_template .env```
1. replace env values: ```nano .env```
1. create venv: ```python3 -m venv .venv```
1. activate venv: ```source .venv/bin/activate```
1. install dependencies: ```pip3 install -r requirements.txt```
1. profit: ```scrapy crawl rent```
1. launch UI: ```streamlit run ui/ui.py```

#### Periodic scan
- suppress the notification test in the .env file using ```SUPPRESS_TEST_NOTIFICATION='True'```
- specify the scan interval in the .env file using ```MINUTES_INTERVAL```
- run: ```python3 main.py```

### Updating dependencies

Dependencies are locked in `requirements.txt` (generated, hashed) from `requirements.in`
(loose, human-edited). Never hand-edit `requirements.txt`. To upgrade:

1. regenerate the lock **inside a Linux python:3.14 container** (required — see note below):
   ```
   docker run --rm -v "$PWD":/app -w /app python:3.14 sh -c \
     "pip install -q --upgrade pip pip-tools && \
      pip-compile --generate-hashes --upgrade --output-file requirements.txt requirements.in"
   ```
2. verify: build the image (`docker build .`) and run a bounded crawl + UI smoke test
3. commit `requirements.txt` and push (CI rebuilds the image)

**Why in a container:** pip-tools 7.x has no universal-lock mode, so it pins for the platform
it runs on. Some deps are platform-gated (e.g. `watchdog`, which streamlit needs only on
non-macOS). Generating the lock on macOS omits them and the Linux image build then fails the
`--require-hashes` install. Always generate on Linux, matching the deploy target.

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