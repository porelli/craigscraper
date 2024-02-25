# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html


# useful for handling different item types with a single interface
from itemadapter import ItemAdapter
import sqlite3
import os
from dotenv import load_dotenv
from apprise import NotifyType
from termcolor import colored


class CraigscraperPipeline:
    def __init__(self):

        load_dotenv()
        rents_db = os.environ.get('RENTS_DB', 'rents.db')

        # initialize sqlite
        self.con = sqlite3.connect(rents_db)
        self.cur = self.con.cursor()
        self.create_tables()

    def create_tables(self):
        self.cur.execute("""CREATE TABLE IF NOT EXISTS listings(
            id INTEGER PRIMARY KEY,
            link TEXT,
            rooms TEXT,
            available_on TEXT,
            size INTEGER,
            attributes BLOB,
            description TEXT,
            title TEXT,
            gym TEXT,
            pool TEXT,
            distance REAL,
            last_price INTEGER,
            last_updated TEXT,
            posted_on TEXT,
            still_published TEXT
        )""")

        self.cur.execute("""CREATE TABLE IF NOT EXISTS prices(
            listing_id INTEGER,
            last_updated TEXT,
            price INTEGER,
            UNIQUE(listing_id, last_updated, price) ON CONFLICT IGNORE
        )""")

        self.cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS listings_ids ON listings(id)""")
        self.cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS listings_links ON listings(link)""")
        self.cur.execute("""CREATE INDEX IF NOT EXISTS prices_ids ON prices(listing_id)""")

    def process_item(self, item, spider):
        self.cur.execute("""INSERT or REPLACE into listings
                            (id, link, rooms, available_on, size, attributes, description, title, gym, pool, distance, last_price, last_updated, posted_on, still_published) VALUES
                            (?,  ?,    ?,     ?,            ?,    ?,          ?,           ?,     ?,   ?,    ?,        ?,          ?,            ?,         ?)""",
                         (
                             item['id'],
                             item['link'],
                             item['rooms'],
                             item['available_on'],
                             item['size'],
                             ', '.join(item['attributes']),
                             item['description'],
                             item['title'],
                             item['gym'],
                             item['pool'],
                             item['distance'],
                             item['price'],
                             item['last_updated'],
                             item['posted_on'],
                             'True'
                         )
        )
        self.cur.execute("""INSERT OR IGNORE INTO prices (listing_id, last_updated, price) VALUES (?, ?, ?)""",
                         (
                             item['id'],
                             item['last_updated'],
                             item['price']
                         )
        )

        self.con.commit()

        # send notifications only if it's not the the first run (file exists)
        if not spider.first_run:
            # check if we had more prices for the same apartment
            self.cur.execute("SELECT price FROM prices WHERE listing_id = ? ORDER BY last_updated DESC", [item['id']])
            data = self.cur.fetchall() # data is an array of tuples

            # if so, we want to build the subject with all the prices
            if len(data) > 1:
                price = ' <- $'.join(str(price[0]) for price in data)
            else:
                price = item['price']

            title = f"${price} / {item['size']}sqft / {item['available_on']} - {item['title']}"
            body = (f"Link: {item['link']}\n"
                        f"Distance from the reference: {item['distance']}\n"
                        f"Gym: {item['gym']}\n"
                        f"Pool: {item['pool']}\n\n"
                        f"Description: {item['description']}")

            spider.notifications.apobj.notify(
                title       = title,
                body        = body,
                notify_type = NotifyType.SUCCESS
            )
        else:
            print(colored('CRAIGSCRAPER RAN FOR THE FIRST TIME, NOTIFICATIONS HAVE BEEN SUPPRESSED', 'magenta'))

        return item
