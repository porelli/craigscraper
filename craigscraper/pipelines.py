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
from apprise import NotifyFormat
from termcolor import colored
from craigscraper.spiders.shared_utils import SharedUtils


class CraigscraperPipeline:
    def __init__(self):

        load_dotenv()
        rents_db = os.environ.get('RENTS_DB', 'rents.db')

        # initialize sqlite
        self.con = sqlite3.connect(rents_db)
        self.cur = self.con.cursor()

        listing_columns = [
            "id INTEGER PRIMARY KEY",
            "link TEXT",
            "rooms TEXT",
            "available_on TEXT",
            "size INTEGER",
            "attributes BLOB",
            "description TEXT",
            "title TEXT",
            "gym TEXT",
            "pool TEXT",
            "parking TEXT",
            "ev_charging TEXT",
            "distance REAL",
            "last_price INTEGER",
            "last_updated TEXT",
            "posted_on TEXT",
            "still_published TEXT"
        ]

        prices_columns = [
            "listing_id INTEGER",
            "last_updated TEXT",
            "price INTEGER"
        ]

        prices_constraints = [
            "UNIQUE(listing_id, last_updated, price) ON CONFLICT IGNORE"
        ]

        self.create_table_if_not_exists('listings', listing_columns)
        self.create_table_if_not_exists('prices', prices_columns, prices_constraints)

    def create_table_if_not_exists(self, table_name, columns, constraints=None):
        # Create table if it doesn't exist
        create_statement = f"""CREATE TABLE IF NOT EXISTS {table_name}({', '.join(columns)}"""
        if constraints:
            create_statement += ", " + ", ".join(constraints)
        create_statement += ")"

        self.cur.execute(create_statement)

        # Get existing column names in the table
        self.cur.execute(f"PRAGMA table_info({table_name});")
        existing_columns = {column[1] for column in self.cur.fetchall()}

        # Columns that can be backfilled
        reprocessable_columns = ['pool', 'gym', 'parking', 'ev_charging']
        utils = SharedUtils()
        # Set row to be returned as dictionary
        self.con.row_factory = sqlite3.Row
        # Create a temporary cursor with the new setting
        special_cur = self.con.cursor()
        # Switch back row select to normal behaviour
        self.con.row_factory = None

        # Add missing columns
        for column in columns:
            column_name = column.split()[0]
            if column_name not in existing_columns:
                alter_statement = f"ALTER TABLE {table_name} ADD COLUMN {column}"
                special_cur.execute(alter_statement)
                print(colored(f"Added missing column: {column_name}", 'green'))
                if column_name in reprocessable_columns:
                    print(colored(f"Re-processing old data...", 'cyan'))

                    special_cur.execute(
                        f"""SELECT *
                        FROM {table_name}
                        WHERE {column_name} IS NULL"""
                    )
                    rows = special_cur.fetchall()

                    for row in rows:
                        id = row['id']
                        # we expect attributes to be a list
                        item = dict(row)
                        item['attributes'] = row['attributes'].split(',')
                        new_value = utils.findFeature(column_name, row)

                        if new_value is not None:  # Only update if new_value is valid
                            self.con.execute(
                                f"""UPDATE {table_name}
                                SET {column_name} = ?
                                WHERE id = ?""", (new_value, id)
                            )

                    print(colored(f"Old data has been re-processed. Updated rows: {len(rows)}", 'green'))
                else:
                    print(colored(f"Old data cannot be re-processed for this column.", 'red'))

        self.con.commit()

    def create_indexes_if_not_exist(self):
        self.cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS listings_ids ON listings(id)""")
        self.cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS listings_links ON listings(link)""")
        self.cur.execute("""CREATE INDEX IF NOT EXISTS prices_ids ON prices(listing_id)""")

    def process_item(self, item, spider):
        # insert or replace if unique index(s) (id OR link) are violated deleting previous row
        self.cur.execute("""INSERT or REPLACE into listings
                            (id, link, rooms, available_on, size, attributes, description, title, gym, pool, parking, distance, last_price, last_updated, posted_on, still_published) VALUES
                            (?,  ?,    ?,     ?,            ?,    ?,          ?,           ?,     ?,   ?,    ?,       ?,        ?,          ?,            ?,         ?)""",
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
                             item['parking'],
                             item['distance'],
                             item['price'],
                             item['last_updated'],
                             item['posted_on'],
                             'True' # always set still_published as true during insert
                         )
        )

        # insert or ignore if unique index(s) (listing_id AND last_updated AND price) are violated deleting previous row
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
            # check if we had more prices for the same apartment and order them from most recent to oldest
            self.cur.execute("SELECT price FROM prices WHERE listing_id = ? ORDER BY last_updated DESC", [item['id']])
            data = self.cur.fetchall() # data is an array of tuples

            # if there are multiple results, we want to build the subject with all the prices
            if len(data) > 1:
                price = ' <- $'.join(str(price[0]) for price in data)
            else:
                price = item['price']

            if item['size'] == None:
                size = "Unknown"
            else:
                size = f"{item['size']}sqft"

            if item['available_on']:
                available_on = f"{item['available_on']}"
            else:
                available_on = "Unknown"

            title = f"${price} / {size} / {available_on} - {item['title']}"
            body = (
                f"Link: {item['link']}\n"
                f"Distance from the reference: {item['distance']}km\n"
                f"Gym: {item['gym']}\n"
                f"Pool: {item['pool']}\n\n"
                f"Parking: {item['parking']}\n\n"
                f"Description: {item['description']}"
            )

            spider.notifications.apobj.notify(
                title       = title,
                body        = body,
                notify_type = NotifyType.SUCCESS,
                body_format = NotifyFormat.TEXT # this is necessary to preserve newlines in the notifications
            )
        else:
            print(colored('CRAIGSCRAPER RAN FOR THE FIRST TIME, NOTIFICATIONS HAVE BEEN SUPPRESSED', 'magenta'))

        return item
