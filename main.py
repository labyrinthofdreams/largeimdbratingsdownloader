"""
The MIT License (MIT)

Copyright (c) 2014-2015 https://github.com/labyrinthofdreams/

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import argparse
import codecs
import csv
import logging
import os.path
import re
import time
import requests
import bs4
from unicodewriter import UnicodeWriter

session = requests.Session()

logfmt = logging.Formatter('%(asctime)s %(message)s')
fh = logging.FileHandler('out.log')
fh.setLevel(logging.DEBUG)
fh.setFormatter(logfmt)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(fh)

def read_cookies(cookie_file):
    imdbcookies = {}
    if cookie_file is None or not os.path.exists(cookie_file):
        return imdbcookies

    try:
        with open(cookie_file, 'rb') as f:
            data = f.read()
            for item in data.split('; '):
                parts = item.split('=', 1)
                imdbcookies[parts[0]] = parts[1]
        return imdbcookies
    except IOError:
        raise

def get_start_positions(last_page, start_from=1):
    cur_page = start_from
    while cur_page <= last_page:
        start_pos = (cur_page - 1) * 250 + 1
        yield (cur_page, start_pos)
        cur_page += 1

def pretty_seconds(t):
    sep = lambda n: (int(n / 60), int(n % 60))
    mins, secs = sep(t)
    hrs, mins = sep(mins)
    return '{0}h {1}m {2}s'.format(hrs, mins, secs)

if __name__ == '__main__':
    opts = argparse.ArgumentParser(description='Download large IMDb rating lists.')
    opts.add_argument('ratings_url', help='URL to IMDb user ratings page')
    opts.add_argument('outfile', help='Path to output CSV file')
    opts.add_argument('--start', type=int, default=1, help='Specify page number to start from')
    opts.add_argument('--cookies', default='cookies.txt', help='Load cookies from file')
    opts.add_argument('--append', action='store_true', help='Append new entries and stop after found all new entries')
    args = opts.parse_args()

    if args.start < 1:
        print 'Start page cannot be less than 1. Setting start page to 1...'
        args.start = 1

    imdbcookies = read_cookies(args.cookies)
    session.cookies = requests.utils.cookiejar_from_dict(imdbcookies)

    logger.debug('Cookies: %s', imdbcookies)

    start_time = time.time()
    # Get number of pages
    print 'Retrieving number of pages'
    uid = re.search('ur([0-9]{8})', args.ratings_url).group(1)
    url = 'http://www.imdb.com/user/ur{0}/ratings?start=1&view=compact&sort=ratings_date:desc&defaults=1'.format(uid)
    logger.info('Retrieving number of pages from %s', url)
    resp = session.get(url)
    while resp.status_code != 200:
        print 'Failed to retrieve number of pages (or private list)'
        print 'Retrying...'
        resp = session.get(url)
    logger.info('Parsing content')
    parsed_html = bs4.BeautifulSoup(resp.text, 'html.parser')
    # Note: There should be only one element called div.desc
    # but there's no guarantee
    pages_text = parsed_html.find('div', class_='desc').get_text()
    num_pages = int(re.search('Page 1 of ([0-9]+)', pages_text).group(1))
    print 'Found {0} pages'.format(num_pages)
    if args.start > num_pages:
        print 'Start page', args.start, 'is greater than found pages:', num_pages
        print 'Setting start pages to last page'
        args.start = num_pages
    imdb_all = []
    username = os.path.splitext(os.path.basename(args.outfile))[0]
    outfile_exists = os.path.exists(args.outfile)
    if not outfile_exists:
        with codecs.open(args.outfile, 'wb') as outfile:
            w = UnicodeWriter(outfile)
            # Only output header if file didn't exist
            w.writerow(['position','const','created','modified','description','Title','Title type','Directors',
                        '{0} rated'.format(username),'IMDb Rating','Runtime (mins)','Year','Genres','Num. Votes',
                        'Release Date (month/day/year)','URL'])
    imdb_old = []
    if args.append:
        with codecs.open(args.outfile, 'rb') as infile:
            r = csv.reader(infile)
            for row in r:
                imdb_old.append(row)
    for cur_page, start_pos in get_start_positions(num_pages, args.start):
        print 'Downloading page', cur_page, 'of', num_pages
        url = 'http://www.imdb.com/user/ur' + uid + '/ratings?start=' + str(start_pos) + '&view=compact&sort=ratings_date:desc&defaults=1'
        logger.info('Downloading page %s: %s', cur_page, url)
        trs = None
        # Try downloading the page until we've received all 201 rows of rating data
        while True:
            resp = session.get(url)
            if resp.status_code != 200:
                print 'Failed to download page', cur_page, '(or private list). Retrying...'
                logger.info('Failed to download page %s (or private list). Retrying...', cur_page)
            html = bs4.BeautifulSoup(resp.text, 'html.parser')
            trs = html.find_all('tr', class_='list_item')
            is_last_page = (cur_page == num_pages)
            if len(trs) != 251 and not is_last_page:
                print 'Error: Received less data than expected (251 ratings, received', len(trs), '). Retrying...'
            else:
                break
        print 'Parsing page', cur_page
        abort = False
        del trs[0] # Skip header
        for tr in trs:
            if abort:
                break
            try:
                if args.append:
                    position = int(imdb_old[-1][0]) + len(imdb_all) + 1
                else:
                    position = unicode(len(imdb_all) + 1)
                html_title = tr.find('td', class_='title')
                imdb_url = html_title.a['href']
                imdb_id = re.search('(tt[0-9]{7})', imdb_url).group(1)
                if 'episode' in html_title['class']:
                    # This ensures that there's a space between
                    # series title and episode name
                    html_title.a.append(' ')
                title = html_title.get_text()
                title_type = tr.find('td', class_='title_type').get_text().strip()
                if title_type == 'Feature':
                    title_type = 'Feature Film'
                rater = tr.select('td.rater_ratings > a')
                if rater:
                    user_rating = rater[0].get_text().strip()
                else:
                    user_rating = tr.find('td', class_='your_ratings').get_text().strip()
                imdb_rating = tr.find('td', class_='user_rating').get_text()
                if imdb_rating == '0.0':
                    imdb_rating = u''
                year = tr.find('td', class_='year').get_text()
                num_votes = tr.find('td', class_='num_votes').get_text().replace(',', '')
                if num_votes == '-':
                    num_votes = u'0'
                url = unicode('http://www.imdb.com' + imdb_url)
                data = [position, imdb_id, u'', u'', u'', title, title_type,
                        u'', user_rating, imdb_rating, u'', year, u'',
                        num_votes, u'', url]
                is_dupe = any(e[1] == imdb_id for e in imdb_all)
                # TV Episodes may have the same show IMDb id
                if is_dupe and title_type not in ('TV Episode', 'TV Series'):
                    print 'Found a duplicate entry:', str(data)
                    logger.info('[%s] Found a duplicate entry: %s', args.outfile, str(data))
                # Check if we reached an entry that already exists
                found_last = any(e[1] == imdb_id for e in imdb_old)
                if args.append and found_last:
                    print 'Found the last new entry. Aborting...'
                    abort = True
                else:
                    with codecs.open(args.outfile, 'ab') as outfile:
                        w = UnicodeWriter(outfile)
                        w.writerow(data)
                    imdb_all.append(data)
            except Exception as e:
                print 'Error: {0}'.format(str(e))
                logger.exception('Error while parsing: %s', str(e))
        if abort:
            break
    end_time = time.time()
    print 'Downloaded', len(imdb_all), 'ratings in', pretty_seconds(end_time - start_time)
    logger.info('Downloaded %s ratings in %s', len(imdb_all), pretty_seconds(end_time - start_time))
    print 'Saved results in', args.outfile
