#!/usr/bin/env python3

from __future__ import print_function
from urllib.parse import urlparse
import urllib3, base64, json, urllib, sys
from optparse import OptionParser
import getpass

def _create_request(url, params='', is_post=False):
    if is_post:
        request = urllib3.Request(url=url, data=params)
    else:
        request = urllib3.Request(url + params)

    request.add_header("Authorization", "Bearer %s" % (access_token))
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    return request

def fetch_and_parse(request):
    response = fetch(request)
    return json.loads(response.read())

def fetch(request):
    try:
        response = urllib3.urlopen(request)
        return response
    except urllib3.HTTPError as e:
        print(e.code)
        print(e.read())
        exit(1)
    except urllib3.URLError as e:
        print(e.args)
        print(e.read())
        exit(1)

def channels_of_member(user_id, team_id):
    return fetch_and_parse(_create_request("%s/users/%s/teams/%s/channels/members" % (base_url, user_id, team_id)))

def find_team(name):
    return fetch_and_parse(_create_request("%s/teams/name/%s" % (base_url, name)))

def get_users_for_team(team_id, page):
    return fetch_and_parse(_create_request("%s/teams/%s/members?per_page=200&page=%d" % (base_url, team_id, page)))

def all_users_of_team(team_id):
    members = []

    i = 0
    while True:
        page = get_users_for_team(team_id, i)
        if len(page) == 0:
            break
        members += page
        i += 1

    print("Found %d members for team %s" % (len(members), team_id))
    return members

def mark_channel_as_read(user_id, channel_id):
    channel = dict()
    channel[u'channel_id'] = channel_id
    channel[u'prev_channel_id'] = ''
    channel_json = json.dumps(channel)
    fetch(_create_request("%s/channels/members/%s/view" % (base_url, user_id), params=channel_json, is_post=True))

def mark_all_channels_of_member_as_read(user_id, team_id):
    print("Marking all channels as read for user %s:" % (user_id))
    channels = channels_of_member(user_id, team_id)
    for c in channels:
        print('.', end='')
        sys.stdout.flush()
        mark_channel_as_read(user_id, c[u'channel_id'])
    print("")

def get_arguments():
    global base_url
    global team_name
    global access_token

    parser = OptionParser(usage =
        '''usage: %prog [options]
        Marks all the channels of all users of given team as read.
        Useful after using the Mattermost bulk import, as otherwise all users have tons of unread messages and
        the Mattermost client has a hard time loading.''')
    parser.add_option("-b", "--base-url",
                      dest="base_url",
                      action="store",
                      type="string",
                      help="Base URL of Mattermost installation (mandatory), e.g. 'https://mattermost.mycompany.ch/'")
    parser.add_option("-t", "--team",
                      dest="team",
                      action="store",
                      type="string",
                      help="Team name of which channels should be marked as read (mandatory)")
    parser.add_option("-a", "--access-token",
                      dest="token",
                      action="store",
                      type="string",
                      help="A valid Mattermost API access token (optional, can be entered interactively)")
    (options, args) = parser.parse_args()

    if options.base_url is None:
        parser.print_help()
        parser.error("Base URL parameter is mandatory")

    if options.team is None:
        parser.error("Team parameter is mandatory")

    if options.token is None:
        access_token = getpass.getpass('Mattermost API access token:')
    else:
        access_token = options.token

    base_url = urlparse.urljoin(options.base_url, '/api/v4')
    team_name = options.team
    print("team_name", team_name)
    print("base_url = ", base_url)
    print("options.base_url", options.base_url)

def main():
    get_arguments()

    team = find_team(team_name)
    members = all_users_of_team(team[u'id'])
    for m in members:
        member_id = m[u'user_id']
        mark_all_channels_of_member_as_read(member_id, team[u'id'])

if __name__ == "__main__":
    main()
