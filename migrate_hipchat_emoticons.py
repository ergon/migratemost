#!/usr/bin/env python

import json
import logging
import os
import urllib3
from functools import reduce
from optparse import OptionParser

import hipchat_api

logger = logging.getLogger(__name__)
logger_handler = logging.StreamHandler()
logger_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger_handler.setFormatter(logger_formatter)
logger.addHandler(logger_handler)
logger.setLevel(logging.INFO)

option_tokens = ''
option_base_url = ''
option_output_path = '.'
option_migrate_global_emoticons = False


def _download_file(url, output_path):
    data = urllib3.urlopen(url)

    with open(output_path, 'wb') as output_file:
        output_file.write(data.read())


def _fetch_emoticons(base_url, tokens):
    emoticons = hipchat_api.fetch_and_parse(base_url + '/emoticon?max-results=1000', tokens)
    return emoticons[u'items']


def _parse_comma_separated_argument(option, opt_str, value, parser):
    setattr(parser.values, option.dest, value.split(','))


def parse_arguments():
    global option_tokens
    global option_base_url
    global option_output_path
    global option_migrate_global_emoticons

    parser = OptionParser(usage='''
        usage: %prog [options]
        Exports emoticons from Hipchat into a Mattermost bulk load compatible format.
    ''')
    parser.add_option('-b', '--base-url',
                      type='string',
                      action='store',
                      dest='base_url',
                      help='Base URL of the Hipchat API, e.g. https://hipchat.mycompany.ch/v2/')
    parser.add_option('-o', '--output-path',
                      type='string',
                      action='store',
                      dest='output_path',
                      help='Path where output files will be placed.')
    parser.add_option('-t', '--tokens',
                      type='string',
                      dest='token_list',
                      action='callback',
                      callback=_parse_comma_separated_argument,
                      help='''Comma separated list of access tokens with "View Group" scope.
Providing many tokens speeds up the the API calls, as Hipchat has a hardcoded 100 requests per token per 5 minutes rate limit.''')
    parser.add_option('--migrate-global-emoticons',
                      dest='migrate_global_emoticons',
                      action='store_true',
                      default=False,
                      help='Migrate not only custom emoticons, but also Hipchat built-in emoticons.')

    (options, args) = parser.parse_args()

    if not options.base_url and not options.token_list:
        parser.print_help()
        exit(1)

    if not options.base_url:
        parser.print_help()
        parser.error("Base URL is mandatory")

    if not options.token_list:
        parser.print_help()
        parser.error("Tokens are mandatory")

    option_base_url = options.base_url
    option_tokens = options.token_list
    option_migrate_global_emoticons = options.migrate_global_emoticons

    if options.output_path:
        option_output_path = options.output_path


def migrate_emoticons(output_path, hipchat_base_url, hipchat_tokens, migrate_global_emoticons=False):
    if not os.path.exists(output_path):
        logger.error("Output path invalid: %s" % output_path)
        exit(1)

    logger.info('Starting Hipchat emoticon migration')
    logger.info('Fetching emoticon list from Hipchat')
    hc_emoticons = _fetch_emoticons(hipchat_base_url, hipchat_tokens)
    if not migrate_global_emoticons:
        hc_emoticons = [e for e in hc_emoticons if e[u'type'] != 'global']
    logger.info('Found %d emoticons' % len(hc_emoticons))
    mm_emojis = []

    for e in hc_emoticons:
        name = 'hc_%s' % e[u'shortcut']  # ensure name is unique
        url = e[u'url']
        filename = url.split('/')[-1]
        download_dir = os.path.abspath('%s/%s' % (output_path, 'emojis'))
        download_path = '%s/%s' % (download_dir, filename)
        if not os.path.exists(download_dir):
            os.mkdir(download_dir)
        logger.info('Downloading emoticon for (%s)' % name)
        _download_file(url, download_path)

        mm_emoji = {'type': 'emoji', 'emoji': {'name': name, 'image': download_path}}
        mm_emojis.append(mm_emoji)

    mm_version = {"type": "version", "version": 1}
    with open('%s/mm_emojis.jsonl' % output_path, 'w') as output_file:
        output_file.write(json.dumps(mm_version) + '\n')
        emoji_json = reduce((lambda a, b: a + json.dumps(b) + '\n'), mm_emojis, '')
        output_file.write(emoji_json)

    logger.info('Finished migrating emoticons')


def main():
    parse_arguments()
    migrate_emoticons(option_output_path, option_base_url, option_tokens, option_migrate_global_emoticons)


if __name__ == "__main__":
    main()
