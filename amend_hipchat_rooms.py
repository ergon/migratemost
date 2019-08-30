#!/usr/bin/env python3

import json
import logging
import os
import hipchat_api
from optparse import OptionParser

logger = logging.getLogger(__name__)
logger_handler = logging.StreamHandler()
logger_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger_handler.setFormatter(logger_formatter)
logger.addHandler(logger_handler)
logger.setLevel(logging.INFO)

option_tokens = ''
option_base_url = ''
option_input_file = './rooms.json'
option_output_path = './'


def _fetch_members(base_url, tokens, room_id):
    members = hipchat_api.fetch_and_parse(base_url + 'room/%d/member?max-results=1000' % room_id, tokens)
    return list(map(lambda m: m[u'id'], members[u'items']))


def _fetch_participants(base_url, tokens, room_id):
    participants = hipchat_api.fetch_and_parse(base_url + 'room/%d/participant?max-results=1000' % room_id, tokens)
    return list(map(lambda p: p[u'id'], participants[u'items']))


def _load_hipchat_rooms(path):
    with open(path, 'r') as hc_rooms_file:
        return json.load(hc_rooms_file)


def _parse_comma_separated_argument(option, opt_str, value, parser):
    setattr(parser.values, option.dest, value.split(','))


def _parse_arguments():
    global option_tokens
    global option_base_url
    global option_input_file
    global option_output_path

    parser = OptionParser(usage='''
        usage: %prog [options]
        Fills member and participant fields of Hipchat room export, as Hipchat export does not fill these fields for public rooms.
    ''')
    parser.add_option('-b', '--base-url',
                      type='string',
                      action='store',
                      dest='option_base_url',
                      help='Base URL of the Hipchat API, e.g. https://hipchat.mycompany.ch/v2/')
    parser.add_option('-i', '--input-file',
                      type='string',
                      action='store',
                      dest='option_input_file',
                      help='Path to rooms.json file')
    parser.add_option('-o', '--output-path',
                      type='string',
                      action='store',
                      dest='option_output_path',
                      help='Path where output file will be placed.')
    parser.add_option('-t', '--option_tokens',
                      type='string',
                      dest='token_list',
                      action='callback',
                      callback=_parse_comma_separated_argument,
                      help='''Comma separated list of access option_tokens with "View Room" scope.
Providing many option_tokens speeds up the the API calls, as Hipchat has a hardcoded 100 requests per token per 5 minutes rate limit.''')

    (options, args) = parser.parse_args()

    if not options.option_base_url and not options.token_list:
        parser.print_help()
        exit(1)

    if not options.option_base_url:
        parser.print_help()
        parser.error("Base URL is mandatory")

    if not options.token_list:
        parser.print_help()
        parser.error("option_tokens are mandatory")

    option_base_url = options.option_base_url
    option_tokens = options.token_list

    if options.option_input_file:
        option_input_file = options.option_input_file

    if options.option_output_path:
        option_output_path = options.option_output_path


def amend_rooms(input_file, output_path, output_filename, hipchat_base_url, hipchat_tokens):
    if not os.path.exists(input_file):
        logger.error("Cannot find input file: %s" % input_file)
        exit(1)

    if not os.path.exists(output_path):
        logger.error("Output path invalid: %s" % output_path)
        exit(1)

    logger.info('Starting to amend Hipchat room export (this might take a while)')
    rooms = _load_hipchat_rooms(input_file)
    for room_container in rooms:
        r = room_container[u'Room']
        room_id = int(r[u'id'])
        logger.debug('updating room: %d: %s' % (room_id, r[u'name']))

        if r[u'is_archived']:
            # skip archived rooms. API does not allow to fetch members for archived rooms.
            logger.debug('\tskipping archived room')
            continue

        r[u'members'] = _fetch_members(hipchat_base_url, hipchat_tokens, room_id)
        r['participants'] = _fetch_participants(hipchat_base_url, hipchat_tokens, room_id)

    output_file_path = '%s/%s' % (output_path, output_filename)
    with open(output_file_path, 'w') as output_file:
        output_file.write(json.dumps(rooms, indent=2))
    logger.info('Finished amending Hipchat room export. Output written to %s' % (output_file_path))


def main():
    _parse_arguments()
    amend_rooms(option_input_file, option_output_path, 'rooms_extended.json', option_base_url, option_tokens)


if __name__ == "__main__":
    main()
