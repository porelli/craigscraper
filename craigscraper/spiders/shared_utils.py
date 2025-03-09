import re


class SharedUtils:
    # regex to extract parking from description
    parking_pattern = re.compile(r'\b(parking|stalls?|spaces?)\b.*?\b(secured|underground|gated|indoor|outdoor|surface|visitor|EV|garage|spot)\b.*?\b(included|available|optional|extra|fee|for)\b.*?(\d+|\w+)?\b', re.IGNORECASE)

    def findFeature(self, feature, item):
        match feature:
            case 'pool':
                result = ("pool" in item['description'])
            case 'gym':
                result = ("gym" in item['description'] or "fitness" in item['description'])
            case 'parking':
                result = bool(self.parking_pattern.search(item['description']))

        # Convert the boolean value to 'True' or 'False' string
        return'True' if result else 'False'