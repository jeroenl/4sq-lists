import json
from collections import defaultdict

import foursquare
import requests_cache
import logging
from datetime import datetime, timedelta

#logging.basicConfig(level=logging.DEBUG)

urllib3_logger = logging.getLogger('requests.packages.urllib3.connectionpool')
urllib3_logger.setLevel(logging.WARNING)

long_cache = {'cache_name': '4sq_long_cache',
              'expire_after': timedelta(days=25),
              'old_data_on_error': True}
short_cache = {'cache_name': '4sq_short_cache',
              'expire_after': timedelta(days=1),
              'old_data_on_error': True}

max_price_tier = 2

with open('lists.json') as listFile:
    lists = json.load(listFile)[::-1]

try:
    with open('oauth_token.txt') as tokenFile:
        token = tokenFile.readline()

    foursq = foursquare.Foursquare(access_token='EB1GGMPOP5P3G4XTNR5SPNFCHYJNVB1Q2LLR5PIOKP3XFQ0C')
except:
    print('Error logging into Foursquare. Place OAuth token in a file called oauth_token.txt')
    print('URL: https://foursquare.com/oauth2/authenticate?client_id=YTCFJEXWPAVDPMBY2D3EIALIHVQAZPBL1L5ICQ0SVUV531MB&response_type=token&redirect_uri=http://foursquare.com')
    exit(1)

import locale
os_encoding = locale.getpreferredencoding()


def get_venues(venue_filter):
    venue_filter = venue_filter.copy()
    if 'limit' in venue_filter:
        venue_filter['limit'] *= 20

    venues = foursq.venues.explore(params=venue_filter)

    return [venue['venue'] for venue in venues['groups'][0]['items']]


def get_venue(venue_id):
    with requests_cache.enabled(**short_cache):
        return foursq.venues(venue_id)['venue']


_categories = defaultdict(list)
def get_categories():
    global _categories
    if _categories:
        return _categories

    with requests_cache.enabled(**long_cache):
        categories = foursq.venues.categories()

    _flatten_categories([], categories['categories'])
    return _categories


def _flatten_categories(parents, categories):
    global _categories
    for cat in categories:
        catName = cat['name']
        for p in parents:
            _categories[p].append(catName)

        _flatten_categories(parents + [catName], cat['categories'])


def is_accepted(compact_venue):
    venue = get_venue(compact_venue['id'])
    return not is_too_expensive(venue) and \
           not is_disliked(venue) and \
           not is_recently_visited(venue) and \
           has_good_ratings(venue) and \
           matches_preferred_time(venue) and \
           matches_category(venue)


def is_too_expensive(venue):
    try:
        price_tier = venue['price']['tier']
    except KeyError:
        logging.debug('No price tier available')
        return False

    logging.debug('Price tier {} (max {})'.format(price_tier, max_price_tier))
    return venue['price']['tier'] > max_price_tier


def is_disliked(venue):
    dislike = venue['dislike']
    if dislike:
        logging.debug('Venue is disliked')

    return dislike


def is_recently_visited(venue):
    try:
        lastVisitedAt = datetime.fromtimestamp(venue['beenHere']['lastVisitedAt'])
    except KeyError:
        logging.debug('Never visited')
        return False

    if 'min_days_since_last_visit' in venue_list:
        revisitAfter = timedelta(days=venue_list['min_days_since_last_visit'])
    else:
        revisitAfter = timedelta(days=180)

    lastVisitedAgo = (datetime.now() - lastVisitedAt)
    logging.debug('Last visited: {} ({} days ago, min {} days)'.format(lastVisitedAt,
                                                                       lastVisitedAgo.days, revisitAfter.days))
    return lastVisitedAgo < revisitAfter


def has_good_ratings(venue):
    try:
        like = venue['like']
        rating = venue['rating']
    except KeyError:
        logging.debug('No rating/liked status available')
        return True

    logging.debug('Rating: {}, Liked: {}'.format(rating, like))

    return like or rating >= 8


def matches_preferred_time(venue):
    if 'preferred_time' not in venue_list:
        logging.debug('No preferred time set')
        return True

    preferred_time = venue_list['preferred_time']
    with requests_cache.enabled(**long_cache):
        venue_hours = foursq.venues.hours(venue['id'])

    days_seen = []
    days_accepted = []

    try:
        timeframes = venue_hours['popular']['timeframes']
    except KeyError:
        try:
            timeframes = venue_hours['hours']['timeframes']
            logging.debug('No popular hours available, using official hours')
        except KeyError:
            logging.debug('No hours information available')
            return True

    for timeframe in timeframes:
        days_seen += timeframe['days']

        matching = next((True for h in timeframe['open'] if int(h['start']) <= preferred_time <= int(h['end'])), False)
        if matching:
            days_accepted += timeframe['days']

    if len(days_seen) != 7:
        logging.warning('Days seen != 7: {}'.format(days_seen))

    logging.debug('Open at {} on {} days (min 5)'.format(preferred_time, len(days_accepted)))

    return len(days_accepted) >= 5


def matches_category(venue):
    if 'category' not in venue_list:
        logging.debug('No preferred category set')
        return True

    preferredCategory = venue_list['category']
    acceptedCategories = get_categories().get(preferredCategory, [])

    for category in venue['categories']:
        if 'primary' not in category:
            continue

        if (category['name'] == preferredCategory
                or category['name'] in acceptedCategories):
            logging.debug('Category ({}) matches preferred category ({})'.
                          format(category['name'], preferredCategory))
            return True
        else:
            logging.debug('Category ({}) does not match preferred category ({})'.
                          format(category['name'], preferredCategory))
            return False

    logging.debug('No category information available')
    return True


def update_list(list_id, new_venues):
    current_venues = foursq.lists(list_id)['list']['listItems']['items']

    new_venue_ids = [v['id'] for v in new_venues]
    current_ids = [v['venue']['id'] for v in current_venues]

    deleted = set(current_ids) - set(new_venue_ids)
    additions = []

    for id in deleted:
        logging.debug('Removing {} from the list'.format(id))
        foursq.lists.deleteitem(list_id, params={'venueId': id})

    for i, venue in enumerate(new_venues):
        venue_id = venue['id']
        itemParams = {'venueId': venue_id}

        if venue_id in current_ids:
            if i == 0:
                continue

            current_item_id = next(v['id'] for v in current_venues if v['venue']['id'] == venue_id)

            prev_venue_id = new_venues[i - 1]['id']
            prev_item_id = next(v['id'] for v in current_venues if v['venue']['id'] == prev_venue_id)
            itemParams['afterId'] = prev_item_id

            logging.debug('Moving {} after item {}'.format(venue['name'], prev_item_id))
            foursq.lists.moveitem(list_id, params={'itemId': current_item_id, 'afterId': prev_item_id})
        else:
            logging.debug('Adding new item {}'.format(venue['name']))
            new_item = foursq.lists.additem(list_id, params={'venueId': venue_id})['item']

            current_venues.append(new_item)
            current_ids.append(new_item['venue']['id'])
            additions.append(venue)

    return additions

all_additions = []

for venue_list in lists:
    print('*** List: {}'.format(venue_list['name']))
    merged_list = []

    for venue_filter in venue_list['filters']:
        logging.debug('** Filter: {}'.format(venue_filter))
        venues = get_venues(venue_filter)

        selected_count = 0
        for venue in venues:
            if 'limit' in venue_filter and selected_count >= venue_filter['limit']:
                continue

            logging.debug('- Venue: {}'.format(venue['name']))

            if not is_accepted(venue):
                logging.debug('Venue was not accepted')
                continue

            if any(venue['id'] == v['id'] for v in merged_list):
                logging.debug('Venue is already in the merged list')
                continue

            logging.debug('Adding venue to the list')

            selected_count += 1
            merged_list.append(venue)

        if selected_count < venue_filter['limit']:
            logging.warning('List is too short!')

    merged_list = sorted(merged_list, key=lambda e: -e.get('rating', 6))

    additions = update_list(venue_list['list_id'], merged_list)
    all_additions += additions

    for venue in merged_list:
        venue_name = venue['name'].encode(os_encoding, 'replace').decode(os_encoding)
        print(venue_name + (' (new)' if venue in additions else ''))

if all_additions:
    print()
    print('[Press enter to close]')
    input()
