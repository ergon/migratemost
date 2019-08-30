#!/usr/bin/env python3

import json
import logging
import time
import urllib3

logger = logging.getLogger(__name__)
logger_handler = logging.StreamHandler()
logger_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger_handler.setFormatter(logger_formatter)
logger.addHandler(logger_handler)

current_token_index = 0
tokens = []


def _get_token():
    global current_token_index

    if current_token_index >= len(tokens):
        logger.info("Exceeded all tokens, waiting for 5mins for rate limit to reset")
        time.sleep(300)
        current_token_index = 0
    return tokens[current_token_index]


def _mark_token_as_exceeded():
    global current_token_index

    logger.debug("Exceeded rate limit for token #" + str(current_token_index))
    current_token_index += 1


def _authorize_url(url):
    token = _get_token()
    seperator = '&' if '?' in url else '?'
    authorized_url = '%s%sauth_token=%s' % (url, seperator, token)
    return authorized_url


def _fetch_with_rate_limit(url):
    authorized_url = _authorize_url(url)
    request = urllib3.Request(authorized_url)

    try:
        return urllib3.urlopen(request)
    except urllib3.URLError as e:
        if e.code == 429:
            _mark_token_as_exceeded()
            return _fetch_with_rate_limit(url)
        else:
            raise e


def fetch_and_parse(url, access_tokens):
    global tokens
    global current_token_index
    tokens = access_tokens
    current_token_index = 0

    try:
        response = _fetch_with_rate_limit(url)
        body = response.read()
        return json.loads(body)
    except urllib3.URLError as e:
        logger.error(e)
        exit(1)
