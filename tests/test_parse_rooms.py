from craigscraper.spiders.shared_utils import SharedUtils

u = SharedUtils()

def test_simple_integer_bath():
    assert u.parse_rooms('1BR / 1Ba') == {'bedrooms': 1.0, 'bathrooms': 1.0, 'bathrooms_type': None}

def test_half_bath():
    assert u.parse_rooms('2BR / 1.5Ba') == {'bedrooms': 2.0, 'bathrooms': 1.5, 'bathrooms_type': None}

def test_split_bath():
    assert u.parse_rooms('1BR / splitBa') == {'bedrooms': 1.0, 'bathrooms': None, 'bathrooms_type': 'split'}

def test_shared_bath():
    assert u.parse_rooms('1BR / sharedBa') == {'bedrooms': 1.0, 'bathrooms': None, 'bathrooms_type': 'shared'}

def test_three_bed_two_bath():
    assert u.parse_rooms('3BR / 2Ba') == {'bedrooms': 3.0, 'bathrooms': 2.0, 'bathrooms_type': None}

def test_malformed_returns_all_none():
    assert u.parse_rooms('garbage') == {'bedrooms': None, 'bathrooms': None, 'bathrooms_type': None}

def test_none_input():
    assert u.parse_rooms(None) == {'bedrooms': None, 'bathrooms': None, 'bathrooms_type': None}
