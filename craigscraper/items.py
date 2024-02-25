# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy


class CraigscraperItem(scrapy.Item):
    # define the fields for your item here like:
    id = scrapy.Field()
    link = scrapy.Field()
    rooms = scrapy.Field()
    available_on = scrapy.Field()
    size = scrapy.Field()
    attributes = scrapy.Field()
    description = scrapy.Field()
    title = scrapy.Field()
    distance = scrapy.Field()
    price = scrapy.Field()
    last_updated = scrapy.Field()
    posted_on = scrapy.Field()
