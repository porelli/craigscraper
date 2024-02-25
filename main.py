import schedule
import time
import os

def job():
    os.system('scrapy crawl rent')
    print('Next job is set to run at: ' + str(schedule.next_run()))

run_every = int(os.environ.get('MINUTES_INTERVAL', '10'))

print('Scheduler initialised')
schedule.every(run_every).minutes.do(lambda: job())
schedule.run_all()

while True:
    schedule.run_pending()
    time.sleep(1)
