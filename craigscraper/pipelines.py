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
            "bedrooms REAL",
            "bathrooms REAL",
            "bathrooms_type TEXT",
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

        # history of content-field edits (forward-only; populated in process_item)
        self.cur.execute("""CREATE TABLE IF NOT EXISTS listing_changes (
            listing_id INTEGER,
            field      TEXT,
            old_value  TEXT,
            new_value  TEXT,
            changed_at TEXT
        )""")
        self.cur.execute("""CREATE INDEX IF NOT EXISTS listing_changes_ids ON listing_changes(listing_id)""")
        self.con.commit()

        self.backfill_rooms()
        self.purge_consecutive_duplicate_prices()

    def purge_consecutive_duplicate_prices(self):
        # One-time cleanup: older data recorded a new prices row whenever `last_updated`
        # changed even if the price didn't, leaving consecutive same-price rows that render
        # as fake "$X -> $X" changes. Delete each price row whose price equals the
        # chronologically-previous row's price for the same listing. Idempotent: once clean,
        # it deletes nothing. Real round-trips (2000->2100->2000) are preserved because only
        # rows equal to their immediate predecessor are removed.
        self.con.row_factory = sqlite3.Row
        cur = self.con.cursor()
        self.con.row_factory = None

        cur.execute("SELECT rowid, listing_id, last_updated, price FROM prices ORDER BY listing_id, last_updated")
        rows = cur.fetchall()
        to_delete = []
        prev_listing = None
        prev_price = None
        for r in rows:
            if r['listing_id'] == prev_listing and r['price'] == prev_price:
                to_delete.append(r['rowid'])
            else:
                prev_listing = r['listing_id']
                prev_price = r['price']

        if not to_delete:
            return

        print(colored(f"Purging {len(to_delete)} consecutive-duplicate price row(s)...", 'cyan'))
        cur.executemany("DELETE FROM prices WHERE rowid = ?", [(rid,) for rid in to_delete])
        self.con.commit()
        print(colored(f"Price dedup complete. Deleted rows: {len(to_delete)}", 'green'))

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

        # Columns that can be recomputed from description/attributes
        reprocessable_columns = ['pool', 'gym', 'parking', 'ev_charging']

        # Add missing columns
        for column in columns:
            column_name = column.split()[0]
            if column_name not in existing_columns:
                alter_statement = f"ALTER TABLE {table_name} ADD COLUMN {column}"
                self.cur.execute(alter_statement)
                print(colored(f"Added missing column: {column_name}", 'green'))

        self.con.commit()

        # Backfill any reprocessable columns that still hold NULL values. This runs on
        # every startup but is idempotent: once every row is populated the SELECT matches
        # nothing, so it's a cheap no-op. It also repairs rows a past bug left as NULL.
        wanted_columns = {c.split()[0] for c in columns}
        for column_name in reprocessable_columns:
            if column_name in wanted_columns:
                self.backfill_null_column(table_name, column_name)

    def backfill_null_column(self, table_name, column_name):
        utils = SharedUtils()

        # fetch rows as dictionaries so we can rebuild the item shape findFeature expects
        self.con.row_factory = sqlite3.Row
        special_cur = self.con.cursor()
        self.con.row_factory = None

        special_cur.execute(
            f"SELECT id, description, attributes FROM {table_name} WHERE {column_name} IS NULL"
        )
        rows = special_cur.fetchall()
        if not rows:
            return

        print(colored(f"Backfilling '{column_name}' for {len(rows)} row(s)...", 'cyan'))
        updated = 0
        for row in rows:
            item = {
                'description': row['description'] or '',
                # attributes are stored as a ', '-joined string; findFeature expects a list
                'attributes': (row['attributes'] or '').split(', '),
            }
            new_value = utils.findFeature(column_name, item)
            if new_value is not None:  # Only update if new_value is valid
                self.con.execute(
                    f"UPDATE {table_name} SET {column_name} = ? WHERE id = ?",
                    (new_value, row['id'])
                )
                updated += 1

        self.con.commit()
        print(colored(f"Backfill complete for '{column_name}'. Updated rows: {updated}", 'green'))

    def backfill_rooms(self):
        utils = SharedUtils()
        self.con.row_factory = sqlite3.Row
        special_cur = self.con.cursor()
        self.con.row_factory = None

        special_cur.execute("SELECT id, rooms FROM listings WHERE bedrooms IS NULL AND rooms IS NOT NULL")
        rows = special_cur.fetchall()
        if not rows:
            return

        print(colored(f"Backfilling parsed rooms for {len(rows)} row(s)...", 'cyan'))
        updated = 0
        for row in rows:
            parsed = utils.parse_rooms(row['rooms'])
            self.con.execute(
                "UPDATE listings SET bedrooms = ?, bathrooms = ?, bathrooms_type = ? WHERE id = ?",
                (parsed['bedrooms'], parsed['bathrooms'], parsed['bathrooms_type'], row['id'])
            )
            updated += 1

        self.con.commit()
        print(colored(f"Rooms backfill complete. Updated rows: {updated}", 'green'))

    def create_indexes_if_not_exist(self):
        self.cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS listings_ids ON listings(id)""")
        self.cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS listings_links ON listings(link)""")
        self.cur.execute("""CREATE INDEX IF NOT EXISTS prices_ids ON prices(listing_id)""")

    def process_item(self, item, spider):
        # record content-field edits before we overwrite the stored row (forward-only history)
        tracked_fields = ['title', 'description', 'attributes', 'available_on', 'size', 'rooms']
        incoming = {
            'title': item['title'],
            'description': item['description'],
            'attributes': ', '.join(item['attributes']),  # stored form
            'available_on': item['available_on'],
            'size': item['size'],
            'rooms': item['rooms'],
        }
        self.cur.execute(
            "SELECT title, description, attributes, available_on, size, rooms FROM listings WHERE id = ?",
            (item['id'],)
        )
        stored = self.cur.fetchone()
        if stored is not None:
            stored_map = dict(zip(tracked_fields, stored))
            for field in tracked_fields:
                old_val = stored_map[field]
                new_val = incoming[field]
                # normalize to string for a stable comparison (DB returns native types)
                if (old_val if old_val is None else str(old_val)) != (new_val if new_val is None else str(new_val)):
                    self.cur.execute(
                        "INSERT INTO listing_changes (listing_id, field, old_value, new_value, changed_at) VALUES (?, ?, ?, ?, ?)",
                        (item['id'], field, None if old_val is None else str(old_val),
                         None if new_val is None else str(new_val), item['last_updated'])
                    )

        # insert or replace if unique index(s) (id OR link) are violated deleting previous row
        self.cur.execute("""INSERT or REPLACE into listings
                            (id, link, rooms, bedrooms, bathrooms, bathrooms_type, available_on, size, attributes, description, title, gym, pool, parking, ev_charging, distance, last_price, last_updated, posted_on, still_published) VALUES
                            (?,  ?,    ?,     ?,        ?,         ?,              ?,            ?,    ?,          ?,           ?,     ?,   ?,    ?,       ?,           ?,        ?,          ?,            ?,         ?)""",
                         (
                             item['id'],
                             item['link'],
                             item['rooms'],
                             item.get('bedrooms'),
                             item.get('bathrooms'),
                             item.get('bathrooms_type'),
                             item['available_on'],
                             item['size'],
                             ', '.join(item['attributes']),
                             item['description'],
                             item['title'],
                             item['gym'],
                             item['pool'],
                             item['parking'],
                             item['ev_charging'],
                             item['distance'],
                             item['price'],
                             item['last_updated'],
                             item['posted_on'],
                             'True' # always set still_published as true during insert
                         )
        )

        # Record a price row only when the price actually differs from this listing's most
        # recent recorded price. Craigslist bumps `last_updated` on re-posts without a price
        # change, and the old UNIQUE(listing_id, last_updated, price) constraint let those
        # through as duplicate-price rows (rendering as fake "$X -> $X" changes). Comparing
        # against only the latest price still captures real round-trips (2000->2100->2000).
        self.cur.execute(
            "SELECT price FROM prices WHERE listing_id = ? ORDER BY last_updated DESC LIMIT 1",
            (item['id'],)
        )
        latest = self.cur.fetchone()
        if latest is None or latest[0] != item['price']:
            self.cur.execute(
                "INSERT OR IGNORE INTO prices (listing_id, last_updated, price) VALUES (?, ?, ?)",
                (item['id'], item['last_updated'], item['price'])
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
