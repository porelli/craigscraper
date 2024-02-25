import scrapy
from datetime import datetime
import geopy.distance
import regex_spm
import sqlite3
import os
import re
from termcolor import colored
from dotenv import load_dotenv
from notifications import Notifications


class RentSpider(scrapy.Spider):
    name = "rent"

    load_dotenv()

    rents_db = os.environ.get('RENTS_DB', 'rents.db')

    min_price         = os.environ.get('MIN_PRICE',         '2000')
    max_price         = os.environ.get('MAX_PRICE',         '2700')
    lat               = os.environ.get('LAT',               '49.2822')
    lon               = os.environ.get('LON',               '-123.1284')
    min_bedrooms      = os.environ.get('MIN_BEDROOMS',      '1')
    search_distance   = os.environ.get('SEARCH_DISTANCE',   '1.41')
    distance_from_lat = os.environ.get('DISTANCE_FROM_LAT', '49.2799016')
    distance_from_lon = os.environ.get('DISTANCE_FROM_LON', '-123.1167676')

    suppress_test_notification = os.environ.get('SUPPRESS_TEST_NOTIFICATION', 'False')

    distance_from = (float(distance_from_lat), float(distance_from_lon))

    allowed_domains = ["craigslist.org"]
    start_urls = [
        f"https://vancouver.craigslist.org/search/vancouver-bc/apa?lat={lat}&lon={lon}&min_price={min_price}&max_price={max_price}&min_bedrooms={min_bedrooms}&search_distance={search_distance}#search=1~list~1~0"
    ]

    # regex to extract availability date from description
    availability_pattern = re.compile(r'^[^\n]*(available|availability|avail)[^\n]*(?P<now>now|immediately|immediate)|((?P<month_long>(January|February|March|April|May|June|July|August|September|October|November|December))|(?P<month_short>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))[\s,]+(?P<day>\d{,2}|)[^\n]*$', flags=re.IGNORECASE | re.MULTILINE)
    # regex to extract id from url
    id_pattern = re.compile(r'(?P<id>\d+).html$', flags=re.IGNORECASE)
    # regex to extract size from title and description
    size_pattern = re.compile(r'(?P<size>\d+)sqft', flags=re.IGNORECASE)

    def __init__(self, notifications_file=None, *args, **kwargs):
        super(RentSpider, self).__init__(*args, **kwargs)

        # initialize notifications
        self.notifications = Notifications(notifications_file)
        print('Sending a test notification to ensure your configuration is valid...')
        if self.suppress_test_notification == 'False':
            print(colored('SENDING TEST NOTIFICATION', 'green'))
            self.notifications.apobj.notify(
                title = 'Looking for apartments',
                body  = 'Starting now...',
            )
        else:
            print(colored('TEST NOTIFICATION SUPPRESSED', 'magenta'))

        # avoid notifications on first execution
        if not os.path.exists(self.rents_db):
            self.first_run = True
            print(colored('DATABASE DOES NOT EXIST, SUPPRESS NOTIFICATIONS', 'magenta'))
        else:
            print(colored('DATABASE EXISTS, NOTIFICATIONS ENABLED', 'green'))
            self.first_run = False

    def parse(self, response):
        links_to_examinate = []

        cl_data = {}
        cl_links = []

        for property in response.css('.cl-static-search-results li')[1:]:
            # title = property.css('li::attr(title)').extract()
            link = property.css('a::attr(href)').get()
            # id = self.get_id(link)
            price = int(''.join(filter(str.isdigit, property.css('div.price::text').get())))

            cl_data[link] = price
            cl_links.append(link)

        # prepare sqlite
        connection = sqlite3.connect(self.rents_db)
        cursor = connection.cursor()

        # find listings already in the database
        cursor.execute('SELECT link, last_price FROM listings WHERE link IN (%s)' %','.join('?'*len(cl_links)), cl_links)
        db_data = dict(cursor.fetchall())
        db_links = list(db_data.keys())

        # listing on both db and cl -> check if price has changed
        common_list = list(set(cl_links).intersection(db_links))
        for listing in common_list:
            if db_data[listing] == cl_data[listing]:
                print(colored('Apartment %s already fetched and price is unchanged: %s'%(self.get_id(listing), listing), 'green'))
            else:
                links_to_examinate.append(listing)
                print(colored('Apartment %s already fetched but price is changed: %s'%(self.get_id(listing), listing), 'yellow'))

        # listings only on db -> update as still_published = 'False'
        only_db_list = list(set(db_links) - set(cl_links))
        if len(only_db_list) > 0:
            print(colored('Apartments have been unpublished: %s'%(' '.join(only_db_list)), 'magenta'))
            cursor.execute('UPDATE listings SET still_published = \'False\' WHERE link IN (%s)' %','.join('?'*len(only_db_list)), only_db_list)

        # listings only on cl -> we need to add them, normal processing
        only_cl_list = list(set(cl_links) - set(db_links))
        for listing in only_cl_list:
            links_to_examinate.append(listing)
            print(colored('Apartment %s is new: %s'%(self.get_id(listing), listing), 'cyan'))

        # continue scraping the links
        for link in links_to_examinate:
            yield scrapy.Request(link, callback = self.parseItem)

    def parseItem(self, response):
        item = {}

        item['attributes'] = response.css('.attrgroup')[2].css('div span::text').getall()
        item['description'] = ' '.join(response.css('section#postingbody::text').getall()).strip()
        item['title'] = response.xpath("//meta[@property='og:title']/@content").extract_first().removesuffix('- craigslist')
        item['link'] = response.xpath("//meta[@property='og:url']/@content").extract_first()
        item['id'] = self.get_id(item['link'])
        geo = tuple(response.xpath("//meta[@name='ICBM']/@content").extract_first().split(', '))
        item['lat'] = geo[0]
        item['lon'] = geo[1]
        item['distance'] = geopy.distance.geodesic(self.distance_from, geo).km
        item['gym'] = str("gym" in item['description']) or str("fitness" in item['description'])
        item['pool'] = str("pool" in item['description'])
        item['price'] = int(''.join(filter(str.isdigit, response.css('span.price').get())))
        times = response.css('div.postinginfos p.postinginfo.reveal time::attr(datetime)').getall()
        item['posted_on'] = times[0]
        if len(times) == 2:
            item['last_updated'] = times[1]
        else:
            item['last_updated'] = times[0]

        item['rooms'] = None
        item['available_on'] = None
        item['size'] = None

        # properties don't have anything to distinguish them and need to be extracted one by one with regexp
        properties = response.css('.attrgroup')[0].css('span::text').getall()
        for attribute in properties:
            attribute_string = attribute.strip()
            match regex_spm.fullmatch_in(attribute_string):
                case r'.*BR.*':
                    item['rooms'] = attribute_string
                case r'.*available.+':
                    match attribute_string:
                        case 'available now':
                            item['available_on'] = datetime.now().date().strftime("%Y-%m-%d")
                        case _:
                            attribute_string = attribute_string + ' 2024' # this is necessary to handle leap years, year will be replaced later
                            available_on = datetime.strptime(attribute_string, 'available %b %d %Y')
                            if available_on.month < datetime.now().month: # this will be next year
                                available_on = available_on.replace(year=(datetime.now().year + 1))
                            else:
                                available_on = available_on.replace(year=datetime.now().year)
                            item['available_on'] = available_on.date().strftime("%Y-%m-%d")
                case r'.*\d+ft.*':
                    item['size'] = int(''.join(filter(str.isdigit, attribute_string)))

        # try to find the date in the description
        if item['available_on'] == None:
            m = self.availability_pattern.search(item['description'])
            if m:
                available_on = datetime.strptime('2024', '%Y') # this is necessary to handle leap years, year will be replaced later
                month_long = m.group('month_long')
                month_short = m.group('month_short')
                day = m.group('day')
                if m.group('now'):
                    item['available_on'] = 'now'
                else:
                    if month_long:
                        available_on = available_on.replace(month=datetime.strptime(month_long, '%B').month)
                    if month_short:
                        available_on = available_on.replace(month=datetime.strptime(month_short, '%b').month)
                    if day:
                        available_on = available_on.replace(day=datetime.strptime(day, '%d').month)
                    if abs(available_on.month - datetime.now().month) < 3: # TODO: this still causes issues and sets listings in the future
                        available_on = available_on.replace(year=(datetime.now().year + 1))
                    else:
                        available_on = available_on.replace(year=datetime.now().year)

                    item['available_on'] = available_on.date().strftime("%Y-%m-%d")

        # try to find the size in the title or description
        if item['size'] == None:
            m = self.size_pattern.search(item['title'])
            if m:
                size = m.group('size')
                if size:
                    item['size'] = size
            else:
                m = self.size_pattern.search(item['description'])
                if m:
                    size = m.group('size')
                    if size:
                        item['size'] = size

        yield item

    def get_id(self, link):
        id_search = self.id_pattern.search(link)

        if id_search.group('id'):
            return int(id_search.group('id'))
        else:
            raise 'Cannot find ID! Something has changed...'