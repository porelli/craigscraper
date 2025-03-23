import re


class SharedUtils:
    # regex to extract parking from description
    parking_pattern = re.compile(
        r'\b('
        r'(parking|stalls?|spaces?)\b.*?\b(secured|underground|gated|indoor|outdoor|surface|visitor|EV|garage|spot|attached|off-street)\b'
        r'|'
        r'(attached\s+garage|off-street\s+parking|carport)'
        r')'
        r'(?:.*?\b(included|available|optional|extra|fee|for)\b)?'
        r'(?:.*?(\d+|\w+))?',
        re.IGNORECASE
    )

    ev_charging_pattern = re.compile(
        r'\b('
        r'(EV|electric vehicle|electric car|EVC|EVCS)\s*(charging|charger|charge|station|port|outlet)'
        r'|'
        r'(charging|charger)\s*(station|port|outlet)?\s*for\s*(EV|electric vehicle|electric car)'
        r')'
        r'(?:\s*(?:is|are|with|has|have|includes?|included|available|ready|enabled|capable|equipped))?'
        r'(?:\s*(?:on[- ]site|on[- ]premises|in[- ]building|in[- ]garage|in[- ]parking))?',
        re.IGNORECASE
    )

    def findFeature(self, feature, item):
        match feature:
            case 'pool':
                result = ("pool" in item['description'])
            case 'gym':
                result = ("gym" in item['description'] or "fitness" in item['description'])
            case 'parking':
                result = bool(self.parking_pattern.search(item['description']) or any(self.parking_pattern.search(attr) for attr in item['attributes']))
            case 'ev_charging':
                result = bool(self.ev_charging_pattern.search(item['description']) or any(self.ev_charging_pattern.search(attr) for attr in item['attributes']))

        # Convert the boolean value to 'True' or 'False' string
        return'True' if result else 'False'