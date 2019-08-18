#!/usr/bin/env python

import base64
import datetime
import glob
import json
import logging
import os
import re
import textwrap
import time
import math
from functools import reduce
from io import BytesIO
from PIL import Image

from optparse import OptionParser, OptionGroup
from unidecode import unidecode

import amend_hipchat_rooms
import migrate_hipchat_emoticons

# Constants
# Arguments:
ALLOWED_AUTH_SERVICES = ['gitlab', 'ldap', 'saml', 'google', 'office365']
ALLOWED_AUTH_DATA_FIELDS = ['username', 'email']
OUTPUT_DELETED_USERS_FILENAME = 'users_to_deactivate'
OUTPUT_ARCHIVED_CHANNELS_FILENAME = 'channels_to_archive'
OUTPUT_FILENAME_PREFIX = 'mm_'
OUTPUT_TEAM_FILENAME = OUTPUT_FILENAME_PREFIX + 'team'
OUTPUT_DIRECT_CHANNELS_FILENAME = OUTPUT_FILENAME_PREFIX + 'direct_channels'
OUTPUT_DIRECT_POSTS_FILENAME = OUTPUT_FILENAME_PREFIX + 'direct_posts'
OUTPUT_CHANNELS_FILENAME = OUTPUT_FILENAME_PREFIX + 'channels'
OUTPUT_CHANNEL_POSTS_FILENAME = OUTPUT_FILENAME_PREFIX + 'channel_posts'
OUTPUT_USERS_FILENAME = OUTPUT_FILENAME_PREFIX + 'users'
OUTPUT_EMOJI_FILENAME = OUTPUT_FILENAME_PREFIX + 'emojis'
OUTPUT_ALL_IN_ONE_FILENAME = OUTPUT_FILENAME_PREFIX + 'all_data'
OUTPUT_HC_ROOMS_AMENDED_FILENAME = 'hc_rooms_amended.json'
INPUT_HC_REDIS_AUTOJOIN_FILENAME = 'autojoin.json'

# Checks:
# https://github.com/mattermost/mattermost-server/blob/cee1e3685968cbf84b8b655bf438fb6d34a612e5/app/file.go#L696
MM_MAX_IMAGE_PIXELS = 24385536
# Default mm configuration in FileSettings: MaxFileSize
MM_MAX_FILE_ATTACHMENT_SIZE_BYTES = 52428800
# By experimentation (admin user tends to get a lot of memberships due to abandoned rooms)
MM_MAX_CHANNEL_MEMBERSHIPS_PER_USER = 375
# Maximum message length in Mattermost
MM_MAX_MESSAGE_LENGTH = 16383

# Regexes
FIRST_CAP_RE = re.compile('(.)([A-Z][a-z]+)')
ALL_CAP_RE = re.compile('([a-z0-9])([A-Z])')
CONSECUTIVE_DASHES_RE = re.compile('[-]{2,}')
TRAILING_DASHES_OR_UNDERSCORES_RE = re.compile('[-,_]*$')
LEADING_DASHES_OR_UNDERSCORES_RE = re.compile('^[-,_]*')

# Other
FORMATTED_JSON_OUTPUT = False  # Mattermost doesn't accept formatted (multiline) JSON, but it's handy for debugging

# Logging
# setup logger
logger = logging.getLogger(__name__)
logger_handler = logging.StreamHandler()
logger_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger_handler.setFormatter(logger_formatter)
logger.addHandler(logger_handler)

# Arguments set on commandline
default_team_name = ''
default_team_display_name = ''
default_auth_service = ''
default_auth_data_field = ''
migration_output_path = '.'
migration_input_path = '.'
option_migrate_direct_posts = False
option_migrate_avatars = False
option_migrate_channels = False
option_migrate_channel_posts = False
option_join_public_channels = False
option_public_membership_based_on_messages = False
option_public_membership_based_on_redis = False
option_skip_archived_rooms = False
option_disable_tutorial = False
option_use_hc_admin_role_as_mm_system_role = False
option_use_hc_admin_role_as_mm_team_role = False
options_map_room_to_town_square = ''
option_filter_hc_users = None
option_concat_import_files = False
option_hipchat_base_url = ''
option_hipchat_tokens = []
option_hipchat_amend_rooms = False
option_migrate_hipchat_custom_emoticons = False
option_migrate_hipchat_builtin_emoticons = False
option_shrink_image_to_limit = False


class Version(int):
    pass


class Team:
    name = ''
    display_name = ''
    type = ''  # "O" for an open team. "I" for an invite-only team.
    description = ''
    allow_open_invite = False
    scheme = ''

    def __init__(self, name, display_name):
        self.name = name
        self.display_name = display_name
        self.type = 'I'


class User:
    _hipchat_id = None
    _deleted = False
    _full_name = ''
    profile_image = ''  # path to image
    username = ''  # unique identifier of user
    email = ''
    nickname = ''
    first_name = ''
    last_name = ''
    position = ''
    roles = ''  # 'system_user' or 'system_user system_admin'
    locale = ''

    auth_service = ''  # defaults to password. other allowed: gitlab, ldap, saml, google, office365
    auth_data = ''  # id attribute if auth_service other than password is used
    password = None  # Only used if password auth_service selected. If none is specified, MM generates a new one

    tutorial_step = ''  # skip tutorial
    show_unread_section = 'false'  # Show unread messages at top of channel sidebar. Yes, a string, not a bool, really.
    military_time = 'true'  # 24h time format. Yes, a string 'false', not a bool, again, really...
    teams = []  # list of UserTeamMembership

    def __init__(self, username, email, hipchat_id):
        self.username = username.lower()
        self.nickname = username.lower()
        self.email = email
        self._hipchat_id = int(hipchat_id)
        self.roles = 'system_user'
        self.locale = 'en'
        self.tutorial_step = '999' if option_disable_tutorial else '1'  # 999 means skip tutorial
        self.show_unread_section = 'false'
        self.military_time = 'true'

    @classmethod
    def from_hc_user(cls, hc_user):
        mm_user = cls(unidecode(hc_user[u'mention_name']), hc_user[u'email'], hc_user[u'id'])
        mm_user.position = hc_user[u'title']
        if option_use_hc_admin_role_as_mm_system_role and 'admin' in hc_user[u'roles']:
            mm_user.roles = 'system_user system_admin'
        mm_user._deleted = hc_user[u'is_deleted']
        mm_user._full_name = hc_user[u'name']
        full_name_parts = mm_user._full_name.rsplit(' ', 1)  # Guessing full name parts
        if len(full_name_parts) == 2:
            mm_user.first_name = full_name_parts[0]
            mm_user.last_name = full_name_parts[1]
        mm_default_membership = UserTeamMembership(default_team_name)
        if option_use_hc_admin_role_as_mm_team_role and 'admin' in hc_user[u'roles']:
            mm_default_membership.roles = 'team_admin team_user'
        mm_user.teams = [mm_default_membership]
        mm_user.auth_service = default_auth_service
        if default_auth_data_field == 'username':
            mm_user.auth_data = mm_user.username
        elif default_auth_data_field == 'email':
            mm_user.auth_data = mm_user.email
        return mm_user

    def has_hc_id(self, hc_id):
        return self._hipchat_id == hc_id

    def get_hc_id(self):
        return self._hipchat_id

    def is_deleted(self):
        return self._deleted


class UserTeamMembership:
    name = ''
    roles = ''  # 'team_user' or 'team_admin team_user'
    channels = []  # list of UserChannelMembership

    def __init__(self, name):
        self.name = name
        self.roles = 'team_user'


class UserChannelMembership:
    name = ''
    roles = ''  # 'channel_user' or 'channel_user channel_admin'
    notify_props = None  # instance of ChannelNotifyProps
    favorite = False

    def __init__(self, name):
        self.name = name
        self.roles = 'channel_user'
        self.favorite = False
        self.notify_props = ChannelNotifyProps()


class ChannelNotifyProps:
    desktop = ''
    mobile = ''
    mark_unread = ''  # 'all' or 'mention' Preference for marking channel as unread.

    def __init__(self):
        self.desktop = 'default'
        self.mobile = 'default'
        self.mark_unread = 'all'


class Channel:
    _archived = False
    _hipchat_id = None
    _hipchat_name = ''
    _admins = set()
    _owner = None
    _members = set()  # 'members': users with membership (only private rooms), 'participants': users currently in a room
    _participants = set()
    team = ''
    name = ''
    display_name = ''
    type = 'O'  # "O" for a public channel. "P" for a private channel.
    header = ''
    purpose = ''

    def __init__(self, team, name, display_name, channel_type, hipchat_id, hipchat_name):
        self.team = team
        self.name = name
        self.display_name = u"%s" % display_name
        self.type = channel_type
        self._hipchat_id = hipchat_id
        self._hipchat_name = hipchat_name

    @classmethod
    def from_hc_room(cls, name, display_name, header, hc_room):
        hc_room_id = int(hc_room['id'])
        channel_type = 'O' if hc_room['privacy'] == 'public' else 'P'
        mm_channel = Channel(default_team_name, name, display_name, channel_type, hc_room_id, hc_room['name'])
        mm_channel.header = header
        mm_channel._archived = hc_room['is_archived']
        mm_channel._admins = set(hc_room['room_admins'])
        mm_channel._members = Channel.hc_members_to_ids(hc_room['members'])
        mm_channel._participants = set(hc_room['participants'])
        mm_channel._owner = hc_room['owner']
        return mm_channel

    @classmethod
    def hc_members_to_ids(cls, members):
        ids = []
        for m in members:
            if isinstance(m, dict):
                ids.append(m['id'])
            else:
                ids.append(m)
        return set(ids)

    def is_private(self):
        return self.type == 'P'

    def get_hc_id(self):
        return self._hipchat_id

    def get_hc_name(self):
        return self._hipchat_name

    def get_channel_members_hc_ids(self):
        mm_members = set()
        mm_members.update(self._members)
        mm_members.update(self._participants)
        return mm_members

    def add_channel_participants(self, participants):
        self._participants.update(participants)

    def get_channel_admins_hc_ids(self):
        mm_admins = set()
        mm_admins.update(self._admins)
        mm_admins.add(self._owner)
        return mm_admins

    def get_cli_id(self):
        return '%s:%s' % (self.team, self.name)


class Post:
    _valid = True
    _user_hc_id = 0
    team = ''
    channel = ''
    user = ''
    message = ''
    create_at = 0
    flagged_by = ''
    replies = []
    reactions = []
    attachments = []

    def __init__(self, team, channel, user, user_hc_id, message, create_at):
        self.team = team
        self.channel = channel
        self.user = user
        self.user_hc_id = user_hc_id
        self.message = message
        self.create_at = create_at

    def get_user_hc_id(self):
        return self._user_hc_id

    def is_valid(self):
        attachments_valid = all([a.is_valid() for a in self.attachments])
        return self._valid and attachments_valid


class DirectChannel:
    members = []  # Must contain a list of members, with a minimum of two usernames and a maximum of eight usernames.
    header = ''
    favorited_by = []

    def __init__(self, members):
        self.members = members


class DirectPost:
    _valid = True
    channel_members = []
    user = ''
    message = ''
    create_at = 0
    flagged_by = ''
    replies = []
    reactions = []
    attachments = []

    def __init__(self, channel_members, user, message, create_at):
        self.channel_members = channel_members
        self.user = user
        self.message = message
        self.create_at = create_at

    def is_valid(self):
        attachments_valid = reduce((lambda a, b: a and b.is_valid()), self.attachments, True)
        return self._valid and attachments_valid


class Attachment:
    _name = ''
    _valid = True
    path = ''  # The path to the file to be attached to the post.

    def __init__(self, path, name):
        self.path = path
        self._name = name

    def get_name(self):
        return self._name

    def is_valid(self):
        return self._valid


# Utility methods

def camel_to_snake_case(name):
    s1 = FIRST_CAP_RE.sub(r'\1_\2', name)
    return ALL_CAP_RE.sub(r'\1_\2', s1).lower()


def to_json(obj):
    class_name = camel_to_snake_case(obj.__class__.__name__)
    wrapped_object = {'type': class_name,
                      class_name: obj}  # MM's JSON structure requires the objects to be wrapped with the type
    json_indent = 4 if FORMATTED_JSON_OUTPUT else None
    return json.dumps(wrapped_object,
                      default=lambda o: {k: v for k, v in o.__dict__.iteritems() if not k.startswith('_')},
                      # skip "private" fields
                      sort_keys=True, indent=json_indent)


def full_output_path(filename, extension='jsonl'):
    return '%s/%s.%s' % (migration_output_path, filename, extension)


def write_mm_json(objects, filename):
    if len(objects) == 0:
        # do not write files with no data lines, as it will crash mm bulk importer
        return
    mm_bulk_load_version = Version(1)
    with open(full_output_path(filename), 'w') as output_file:
        output_file.writelines(to_json(mm_bulk_load_version) + "\n")
        for o in objects:
            output_file.writelines(to_json(o) + "\n")


def write_space_separated_list(collection, filename):
    with open(full_output_path(filename, 'txt'), 'w') as output_file:
        output_file.write(' '.join(collection))


def timestamp_from_date(date):
    d = datetime.datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ %f")
    return int(time.mktime(d.timetuple())) * 1000 + d.microsecond / 1000


def sanitize_message(message):
    # Translate Hipchat formatting to Mattermost and split too long messages
    # List of slash commands in Hipchat:
    # https://confluence.atlassian.com/hipchatdc3/keyboard-shortcuts-and-slash-commands-966656108.html
    message_parts = ['']
    if message.startswith("/code"):
        sliced = textwrap.wrap(message[6:],
                               MM_MAX_MESSAGE_LENGTH - 8)  # shorten 8 to make room for formatting characters
        message_parts = ["```\n%s\n```" % m for m in sliced]
    elif message.startswith("/quote"):
        sliced = textwrap.wrap(message[7:],
                               MM_MAX_MESSAGE_LENGTH - 3)  # shorten 3 to make room for formatting characters
        message_parts = ["> %s\n" % m for m in sliced]
    else:
        message_parts = textwrap.wrap(message, MM_MAX_MESSAGE_LENGTH)

    return message_parts if len(message_parts) > 0 else ['']


def is_invalid_image(full_attachment_path):
    try:
        image = Image.open(full_attachment_path)
        pixels = image.width * image.height
    except:
        return False  # not an image

    if pixels >= MM_MAX_IMAGE_PIXELS:
        if option_shrink_image_to_limit:
            image = get_shrinked_image(image, pixels)
            logger.debug("Resized image %s to (%d,%d)" % (full_attachment_path, image.width, image.height))
            try:
                image.save(full_attachment_path)
            except ValueError as e:
                logger.warning("Failed to resize image %s: %s" % (full_attachment_path, str(e)))
                return True  # failed to resize, so image is still too large for uploading
            return False
        else:
            return True  # image too large for uploading
    else:
        return False  # valid image


def get_shrinked_image(img, pixels):
    scale_ratio = float(pixels) / MM_MAX_IMAGE_PIXELS
    root = math.sqrt(scale_ratio)
    new_width = int(img.width / root)
    new_height = int(img.height / root)

    return img.resize((new_width, new_height))


def is_valid_attachment(full_attachment_path):
    if not os.path.exists(full_attachment_path):
        logger.debug("Invalid attachment: no file found at %s" % full_attachment_path)
        return False
    if os.path.getsize(full_attachment_path) >= MM_MAX_FILE_ATTACHMENT_SIZE_BYTES:
        logger.debug("Invalid attachment: too large file found at %s" % full_attachment_path)
        return False
    if is_invalid_image(full_attachment_path):
        logger.debug("Invalid attachment: image is invalid at %s" % full_attachment_path)
        return False
    return True


def contains_unicode(s):
    try:
        s.encode(encoding='utf-8').decode('ascii')
    except UnicodeDecodeError:
        return True
    else:
        return False


def sanitize_name(name):
    name = unidecode(u'%s' % name) if contains_unicode(name) else name
    name = name.lower()
    name = re.sub('\s', '-', name)  # whitespaces are not allowed
    # by default, all non alphanumeric charaters will be replaced with underscore
    # if this is a too broad replacement for you or causes clashes of channel names,
    # add any special requirements for channel name conversion here
    name = re.sub('[^a-z0-9\-]', '_', name)
    name = re.sub('^(\d)$', 'number_\\1', name)  # leading digits are not allowed
    name = CONSECUTIVE_DASHES_RE.sub('-', name)  # beautification, multiple dashes in a row are allowed
    name = TRAILING_DASHES_OR_UNDERSCORES_RE.sub('', name)  # trailing dashes or underscores are not allowed
    name = LEADING_DASHES_OR_UNDERSCORES_RE.sub('', name)  # leading dashes or underscores are not allowed
    return str(name.decode('UTF-8'))


def sanitize_channel_display_name_or_header(name):
    name = re.sub(r'\\', r'\\\\', name)
    name = re.sub('"', r'\"', name)
    return name


def load_hipchat_users():
    with open('%s/users.json' % migration_input_path, 'r') as hc_users_file:
        users = json.load(hc_users_file)
        flattened_users = [u[u'User'] for u in users]
        return flattened_users


def load_hipchat_user_history(user_id):
    user_history_path = '%s/users/%d/history.json' % (migration_input_path, user_id)
    if not os.path.exists(user_history_path):
        return []  # ignore missing history files, required for users that were deleted in Hipchat
    with open(user_history_path, 'r') as hc_history_file:
        return json.load(hc_history_file)


def load_hipchat_rooms():
    input_file = '%s/%s' % (
        migration_output_path, OUTPUT_HC_ROOMS_AMENDED_FILENAME) if option_hipchat_amend_rooms \
        else '%s/rooms.json' % migration_input_path
    with open(input_file, 'r') as hc_rooms_file:
        rooms = json.load(hc_rooms_file)
        flattened_rooms = list(map(lambda u: u[u'Room'], rooms))
        return flattened_rooms


def load_hipchat_room_history(room_id):
    with open('%s/rooms/%d/history.json' % (migration_input_path, room_id), 'r') as hc_history_file:
        room_history = json.load(hc_history_file)
        # ignoring the following message types:
        # - "NotificationMessage"
        # - "GuestAccessMessage"
        # - "ArchiveRoomMessage"
        # - "TopicRoomMessage"
        user_messages = [m for m in room_history if 'UserMessage' in m]
        flattened_messages = [m['UserMessage'] for m in user_messages]
        return flattened_messages


def load_redis_autojoin():
    with open('%s/%s' % (migration_input_path, INPUT_HC_REDIS_AUTOJOIN_FILENAME), 'r') as hc_autojoin_file:
        autojoins = json.load(hc_autojoin_file)
        return autojoins['autojoins']


def concat_files(input_file_paths, output_file_name):
    with open(full_output_path(output_file_name), 'w') as output_file:
        mm_bulk_load_version = Version(1)
        output_file.writelines(to_json(mm_bulk_load_version) + "\n")
        for input_file_path in input_file_paths:
            with open(input_file_path, 'r') as input_file:
                iter_lines = iter(input_file)
                next(iter_lines)  # skip version line as it should only occur once per file
                output_file.writelines(iter_lines)


def migrate_team():
    return Team(default_team_name, default_team_display_name)


def store_base64_image(data, path, filename):
    try:
        decoded = base64.b64decode(data)
    except TypeError as e:
        logger.error("Failed to decode base 64 data (%s): %s" % (str(e), data))
        return ''

    img = Image.open(BytesIO(decoded))
    img_path = '%s/%s.%s' % (path, filename, lower(img.format))
    with open(img_path, 'wb') as f:
        img.save(f)
    return img_path


def migrate_users():
    hc_users = load_hipchat_users()
    mm_users = []

    avatar_output_path = '%s/avatars' % migration_output_path
    if option_migrate_avatars and not os.path.exists(avatar_output_path):
        os.makedirs(avatar_output_path)

    for hc_user in hc_users:
        if option_filter_hc_users and not re.match(option_filter_hc_users, hc_user['email']):
            continue

        mm_user = User.from_hc_user(hc_user)
        if hc_user['avatar'] is not None and option_migrate_avatars:
            avatar_path = store_base64_image(hc_user['avatar'], avatar_output_path,
                                             'mm_user_%s_avatar' % mm_user.get_hc_id())
            mm_user.profile_image = avatar_path
        mm_users.append(mm_user)

    deleted_users = list(filter(lambda u: u.is_deleted(), mm_users))
    deleted_users_usernames = set(map(lambda u: u.username, deleted_users))

    if len(deleted_users) > 0:
        logger.info(
            '\tFound %d deleted users. Writing file %s.txt to be used with Mattermost CLI to deactivate them.' % (
                len(deleted_users), OUTPUT_DELETED_USERS_FILENAME))
        write_space_separated_list(sorted(deleted_users_usernames), OUTPUT_DELETED_USERS_FILENAME)

    return mm_users


def migrate_direct_posts(mm_username_by_hc_id, mm_user):
    hc_user_id = mm_user.get_hc_id()
    hc_user_history = load_hipchat_user_history(hc_user_id)

    mm_direct_posts = []
    invalid_post_count = 0
    for hc_post in hc_user_history:
        hc_message = hc_post['PrivateUserMessage']
        sender_hc_id = hc_message['sender']['id']
        receiver_hc_id = hc_message['receiver']['id']

        # only consider messages where current was sender, otherwise messages will be duplicated
        if sender_hc_id != hc_user_id:
            continue

        try:
            sender_mm_username = mm_username_by_hc_id[sender_hc_id]
        except KeyError:
            logger.error('Could not find sender with Hipchat ID %s of direct post' % sender_hc_id)
            exit(1)
        try:
            receiver_mm_username = mm_username_by_hc_id[receiver_hc_id]
        except KeyError:
            logger.error('Could not find receiver with Hipchat ID %s of direct post' % receiver_hc_id)
            exit(1)
        timestamp = timestamp_from_date(hc_message['timestamp'])
        message_parts = sanitize_message(hc_message['message'])

        mm_current_posts = []
        for i, part in enumerate(message_parts):
            mm_post = DirectPost([sender_mm_username, receiver_mm_username], sender_mm_username, part, timestamp + i)
            mm_current_posts.append(mm_post)

        if hc_message['attachment'] is not None:
            mm_attachment = migrate_attachment(hc_message['attachment'], 'users')
            mm_current_posts[0].attachments = [mm_attachment]

        if not all([p.is_valid() for p in mm_current_posts]):
            invalid_post_count += 1
        else:
            mm_direct_posts.extend(mm_current_posts)

    if invalid_post_count > 0:
        logger.warning('\t\tSkipped %d invalid direct posts of user %s' % (invalid_post_count, mm_user.username))

    return mm_direct_posts


def migrate_attachment(hc_attachment, subpath):
    hc_attachment_path = u"%s" % hc_attachment['path']
    hc_attachment_name = u"%s" % hc_attachment['name']
    full_attachment_path = "%s/%s/files/%s" % (migration_input_path, subpath, hc_attachment_path)
    mm_attachment = Attachment(full_attachment_path, hc_attachment_name)

    if not is_valid_attachment(full_attachment_path):
        mm_attachment._valid = False
        logger.warning("Found invalid attachment %s" % to_json(mm_attachment))

    return mm_attachment


def migrate_direct_channels(direct_channel_user_pairs):
    unique_user_pairs = set(direct_channel_user_pairs)  # given user_pairs is a list of frozenset
    mm_direct_channels = []
    for p in unique_user_pairs:
        if len(p) == 1:
            # fix 1:1 chats with oneself as frozenset eliminates e.g. (1,1) to (1)
            only_member = next(iter(p))
            mm_direct_channels.append(DirectChannel([only_member, only_member]))
        else:
            mm_direct_channels.append(DirectChannel(list(p)))

    return mm_direct_channels


def migrate_channels():
    hc_rooms = load_hipchat_rooms()
    mm_channels = []
    mm_archived_channels = []
    for hc_room in hc_rooms:
        hc_room_archived = hc_room['is_archived']
        if option_skip_archived_rooms and hc_room_archived:
            logger.info('Skipping archived room %d' % int(hc_room['id']))
            continue

        if options_map_room_to_town_square == hc_room['name']:
            name = 'town-square'
            display_name = 'Town Square'
        else:
            name = sanitize_name(hc_room['name'])
            if not name:
                # channel name contained only invalid characters
                name = 'channel_hc_%s' % hc_room['id']
            display_name = sanitize_channel_display_name_or_header(hc_room['name'])

        header = sanitize_channel_display_name_or_header(hc_room['topic'])
        mm_channel = Channel.from_hc_room(name, display_name, header, hc_room)
        mm_channels.append(mm_channel)
        if hc_room_archived:
            mm_archived_channels.append(mm_channel)

    if len(mm_archived_channels) > 0:
        mm_unique_cli_style_team_channels = set(map(lambda c: c.get_cli_id(), mm_archived_channels))
        logger.info(
            '\tFound %d archived channels. Writing file %s.txt to be used with Mattermost CLI to archive them.' % (
                len(mm_unique_cli_style_team_channels), OUTPUT_ARCHIVED_CHANNELS_FILENAME))
        write_space_separated_list(mm_unique_cli_style_team_channels, OUTPUT_ARCHIVED_CHANNELS_FILENAME)

    return mm_channels


def migrate_channel_posts(mm_username_by_hc_id, mm_channel):
    hc_room_history = load_hipchat_room_history(mm_channel.get_hc_id())

    invalid_post_count = 0
    mm_posts = []
    for hc_message in hc_room_history:
        timestamp = timestamp_from_date(hc_message['timestamp'])
        sender_hc_id = hc_message['sender']['id']
        sender_mm_username = mm_username_by_hc_id[sender_hc_id]
        message_parts = sanitize_message(hc_message['message'])

        mm_current_posts = []
        for i, part in enumerate(message_parts):
            mm_post = Post(default_team_name, mm_channel.name, sender_mm_username, sender_hc_id, part, timestamp + i)
            mm_current_posts.append(mm_post)

        if 'attachment' in hc_message and hc_message['attachment'] is not None:
            mm_attachment = migrate_attachment(hc_message['attachment'], 'rooms/%d' % mm_channel.get_hc_id())
            mm_current_posts[0].attachments = [mm_attachment]

        if not all([p.is_valid() for p in mm_current_posts]):
            invalid_post_count += 1
        else:
            mm_posts.extend(mm_current_posts)

    if invalid_post_count > 0:
        logger.warning("Skipped %d invalid channel posts of room %s" % (invalid_post_count, mm_channel.name))

    return mm_posts


def migrate_user_channel_membership(mm_channels, mm_user):
    member_of_channels = list(filter(lambda c: mm_user.get_hc_id() in c.get_channel_members_hc_ids(), mm_channels))
    admin_of_channels = list(filter(lambda c: mm_user.get_hc_id() in c.get_channel_admins_hc_ids(), mm_channels))

    if not option_join_public_channels:
        member_of_channels = list(filter(lambda c: c.is_private(), member_of_channels))

    member_of_channels_names = set(map(lambda c: c.name, member_of_channels))
    admin_of_channels_names = set(map(lambda c: c.name, admin_of_channels))

    all_channels_names = member_of_channels_names
    all_channels_names.update(admin_of_channels_names)

    channel_memberships = []
    for channel_name in all_channels_names:
        membership = UserChannelMembership(channel_name)
        if channel_name in admin_of_channels_names:
            membership.roles = 'channel_user channel_admin'
        channel_memberships.append(membership)

    return channel_memberships


def redis_participants_by_room_name():
    autojoins = load_redis_autojoin()
    participants_by_room_name = dict()
    for a in autojoins:
        hc_user_id = a['user_id']
        hc_room_names_to_join = [r['name'] for r in a['rooms'] if r['jid'].endswith(
            'conf.btf.hipchat.com')]  # filter out 1:1 chats ending on chat.btf.hipchat.com
        for room_name in hc_room_names_to_join:
            participants_by_room_name.setdefault(room_name, []).append(hc_user_id)
    return participants_by_room_name


def _parse_comma_separated_argument(option, opt_str, value, parser):
    setattr(parser.values, option.dest, value.split(','))


def parse_arguments():
    global default_team_name
    global default_team_display_name
    global default_auth_service
    global default_auth_data_field
    global migration_input_path
    global migration_output_path
    global option_migrate_channels
    global option_migrate_channel_posts
    global option_migrate_direct_posts
    global option_migrate_avatars
    global option_join_public_channels
    global option_public_membership_based_on_messages
    global option_public_membership_based_on_redis
    global option_skip_archived_rooms
    global option_disable_tutorial
    global option_use_hc_admin_role_as_mm_team_role
    global option_use_hc_admin_role_as_mm_system_role
    global options_map_room_to_town_square
    global option_filter_hc_users
    global option_concat_import_files
    global option_hipchat_base_url
    global option_hipchat_tokens
    global option_hipchat_amend_rooms
    global option_migrate_hipchat_custom_emoticons
    global option_migrate_hipchat_builtin_emoticons
    global option_shrink_image_to_limit

    parser = OptionParser(usage=
                          '''usage: %prog [options]
        Converts a Hipchat export to Mattermost bulk import files.
        By default, only the team and users are migrated, for more options see "Migration Options"''')
    parser.add_option("-t", "--team",
                      dest="default_team_display_name",
                      action="store",
                      type="string",
                      help="Default team name to which all users are assigned")
    parser.add_option("-o", "--output-path",
                      dest="output_path",
                      action="store",
                      type="string",
                      help="Output path where migration files will be placed. Defaults to current directory.")
    parser.add_option("-i", "--input-path",
                      dest="input_path",
                      action="store",
                      type="string",
                      help="Path to Hipchat export (the 'data' directory of the extracted export. Defaults to current directory.)")
    parser.add_option("-v", "--verbose",
                      dest="verbose",
                      action="store_true",
                      default=False,
                      help="Enable verbose logging")
    parser.add_option("--concat-output",
                      dest="concat_output_files",
                      action="store_true",
                      default=False,
                      help="Concatenate all output files into one after conversion is done. Mattermost bulk import seems to be much faster with one large files instead of many smaller ones.")

    parser_migration_group = OptionGroup(parser, "Migration Options",
                                         "These options control what data should be migrated")
    parser_migration_group.add_option("--migrate-all",
                                      dest="migrate_all",
                                      action="store_true",
                                      default=False,
                                      help="Use to migrate everything (recommended)")
    parser_migration_group.add_option("--migrate-direct-posts",
                                      dest="migrate_direct_posts",
                                      action="store_true",
                                      default=False,
                                      help="Use to migrate direct posts (1:1 in Hipchat)")
    parser_migration_group.add_option("--migrate-channels",
                                      dest="migrate_channels",
                                      action="store_true",
                                      default=False,
                                      help="Use to migrate channels without the posts (rooms in Hipchat)")
    parser_migration_group.add_option("--migrate-channel-posts",
                                      dest="migrate_channel_posts",
                                      action="store_true",
                                      default=False,
                                      help="Use to migrate channels including the posts (rooms and messages in Hipchat)")
    parser_migration_group.add_option("--migrate-avatars",
                                      dest="migrate_avatars",
                                      action="store_true",
                                      default=False,
                                      help="Use to migrate users avatars")
    parser_migration_group.add_option("--skip-archived-rooms",
                                      dest="skip_archived_rooms",
                                      action="store_true",
                                      default=False,
                                      help="Use to to not migrate rooms that are marked as archived in Hipchat")
    parser_migration_group.add_option("--disable-tutorial",
                                      dest="disable_tutorial",
                                      action="store_true",
                                      default=False,
                                      help="Use to to disable introductory tutorial of Mattermost upon first logon for all users")
    parser_migration_group.add_option("--shrink-image-to-limit",
                                      dest="shrink_image_to_limit",
                                      action="store_true",
                                      default=False,
                                      help="Shrink images to their maximum size allowed by Mattermost")

    public_room_membership_intro = 'Use to have users join public channels if they were member of the corresponding room in Hipchat.'
    public_room_membership_disclaimer = 'DISCLAIMER: Getting reliable public room memberships out of Hipchat is not easy. See README.md for more details.'
    parser_migration_group.add_option("--public-channel-membership-based-on-hipchat-export",
                                      dest="public_channel_membership_based_on_export",
                                      action="store_true",
                                      default=False,
                                      help='%s Room membership is evaluated based on Hipchat export and can be amended using the "--amend-rooms" option. %s' % (
                                          public_room_membership_intro, public_room_membership_disclaimer))
    parser_migration_group.add_option("--public-channel-membership-based-on-messages",
                                      dest="public_channel_membership_based_on_messages",
                                      action="store_true",
                                      default=False,
                                      help='%s Room membership is based on if a user has ever written a message in the room. %s' % (
                                          public_room_membership_intro, public_room_membership_disclaimer))
    parser_migration_group.add_option("--public-channel-membership-based-on-redis-export",
                                      dest="public_channel_membership_based_on_redis",
                                      action="store_true",
                                      default=False,
                                      help='%s Room membership is evaluated using a Redis export. Requires the output of the "redis_autjoin.sh" script to be at the input path. %s' % (
                                          public_room_membership_intro, public_room_membership_disclaimer))

    parser_migration_group.add_option("--apply-admin-team-role",
                                      dest="apply_admin_team_role",
                                      action="store_true",
                                      default=False,
                                      help="Use to give users team admin role in Mattermost if they had Hipchat admin rights.")
    parser_migration_group.add_option("--apply-admin-system-role",
                                      dest="apply_admin_system_role",
                                      action="store_true",
                                      default=False,
                                      help="Use to give users system admin role in Mattermost if they had Hipchat admin rights.")
    parser_migration_group.add_option("--map-town-square-channel",
                                      dest="town_square_source_room_name",
                                      action="store",
                                      type="string",
                                      help="Map a Hipchat room to the town-square channel (default channel)"
                                      )
    parser_migration_group.add_option("--filter-users",
                                      dest="filter_users",
                                      action="store",
                                      type="string",
                                      help="Filter Hipchat users by e-mail address using regex (important: filtered users must not occur in chat history)"
                                      )

    parser_hipchat_group = OptionGroup(parser, "Hipchat Export Options",
                                       "These options control data which will be fetched from Hipchat to amend the export")
    parser_hipchat_group.add_option("--amend-rooms",
                                    dest="amend_rooms",
                                    action="store_true",
                                    default=False,
                                    help="Use to amend rooms exported by Hipchat with proper participant and member lists (recommended)")
    parser_hipchat_group.add_option("--migrate-custom-emoticons",
                                    dest="migrate_custom_emoticons",
                                    action="store_true",
                                    default=False,
                                    help="Use to migrate custom Hipchat emoticons")
    parser_hipchat_group.add_option("--migrate-builtin-emoticons",
                                    dest="migrate_builtin_emoticons",
                                    action="store_true",
                                    default=False,
                                    help="Use to migrate Hipchat built-in emoticons")
    parser_hipchat_group.add_option("--hipchat-base-url",
                                    dest="hipchat_base_url",
                                    action="store",
                                    type="string",
                                    help="Base URL of the Hipchat API, e.g. https://hipchat.mycompany.ch/v2/")
    parser_hipchat_group.add_option("--hipchat-access-tokens",
                                    dest='hipchat_token_list',
                                    type='string',
                                    action='callback',
                                    callback=_parse_comma_separated_argument,
                                    help='''Comma-separated list of access option_tokens with "View Room" and "View Group" scope.
Providing many option_tokens speeds up the the API calls, as Hipchat has a hardcoded 100 requests per token per 5 minutes rate limit.''')

    parser_authentication_group = OptionGroup(parser, "Authentication Options",
                                              "These options control what authentication settings should be applied to the migrated users.")
    parser_authentication_group.add_option("--authentication-service",
                                           dest="authentication_service",
                                           action="store",
                                           type="string",
                                           help="Which authentication type to use, defaults to password (Mattermost built-in authentication).\nIf provided, must be one of: %s" % ', '.join(
                                               ALLOWED_AUTH_SERVICES))
    parser_authentication_group.add_option("--authentication-data-field",
                                           dest="authentication_data_field",
                                           action="store",
                                           type="string",
                                           help="Which user field to use for authentication service, only relevant if other than Mattermost built-in service is used.\nValid choices are: %s" % ', '.join(
                                               ALLOWED_AUTH_DATA_FIELDS))

    parser.add_option_group(parser_migration_group)
    parser.add_option_group(parser_authentication_group)
    parser.add_option_group(parser_hipchat_group)

    (options, args) = parser.parse_args()

    if options.default_team_display_name is None:
        parser.print_help()
        parser.error("Team name is mandatory")
    else:
        default_team_display_name = options.default_team_display_name
        default_team_name = sanitize_name(options.default_team_display_name)

    if not options.output_path is None:
        migration_output_path = os.path.abspath(options.output_path)
        if not os.path.exists(migration_output_path):
            parser.error("Provided output path does not exist.")

    if not options.input_path is None:
        migration_input_path = os.path.abspath(options.input_path)
        if not os.path.exists(migration_input_path):
            parser.error("Provided input path does not exist.")

    if options.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if options.concat_output_files:
        option_concat_import_files = True

    if options.skip_archived_rooms:
        option_skip_archived_rooms = True

    if options.disable_tutorial:
        option_disable_tutorial = True

    if options.migrate_channels:
        option_migrate_channels = True

    if options.migrate_channel_posts:
        option_migrate_channels = True
        option_migrate_channel_posts = True

    if options.migrate_direct_posts:
        option_migrate_direct_posts = True

    if options.migrate_avatars:
        option_migrate_avatars = True

    if options.migrate_all:
        option_migrate_direct_posts = True
        option_migrate_channels = True
        option_migrate_channel_posts = True
        option_migrate_avatars = True

    if options.apply_admin_team_role:
        option_use_hc_admin_role_as_mm_team_role = True

    if options.apply_admin_system_role:
        option_use_hc_admin_role_as_mm_system_role = True

    if options.town_square_source_room_name:
        options_map_room_to_town_square = options.town_square_source_room_name

    if options.filter_users:
        option_filter_hc_users = options.filter_users

    if options.amend_rooms or options.migrate_custom_emoticons or options.migrate_builtin_emoticons:
        if not options.hipchat_base_url or not options.hipchat_token_list:
            parser.error("Hipchat base url and tokens required to amend rooms or migrating emoticons.")
        option_hipchat_base_url = options.hipchat_base_url
        option_hipchat_tokens = options.hipchat_token_list
        option_hipchat_amend_rooms = options.amend_rooms
        option_migrate_hipchat_custom_emoticons = options.migrate_custom_emoticons
        option_migrate_hipchat_builtin_emoticons = options.migrate_builtin_emoticons

    if options.public_channel_membership_based_on_export or options.public_channel_membership_based_on_messages or options.public_channel_membership_based_on_redis:
        option_join_public_channels = True

    if options.public_channel_membership_based_on_messages:
        option_public_membership_based_on_messages = True

    if options.public_channel_membership_based_on_redis:
        redis_export_path = '%s/%s' % (migration_input_path, INPUT_HC_REDIS_AUTOJOIN_FILENAME)
        if not os.path.exists(redis_export_path):
            parser.error("This option requires a Redis export to be present at %s" % redis_export_path)
        option_public_membership_based_on_redis = True

    if options.authentication_service:
        default_auth_service = options.authentication_service.lower()
        if not default_auth_service in ALLOWED_AUTH_SERVICES:
            parser.error("Illegal authentication service")

        if default_auth_service == 'ldap':
            if options.authentication_data_field:
                default_auth_data_field = options.authentication_data_field.lower()
                if not default_auth_data_field in ALLOWED_AUTH_DATA_FIELDS:
                    parser.error("Illegal authentication data field")
            else:
                default_auth_data_field = 'username'
        elif default_auth_service == 'password':
            default_auth_service = ''
            default_auth_data_field = ''
    else:
        default_auth_service = ''
        default_auth_data_field = ''

    if options.shrink_image_to_limit:
        option_shrink_image_to_limit = True


def main():
    parse_arguments()

    stats_total_users = 0
    stats_total_direct_posts = 0
    stats_total_channels = 0
    stats_total_channel_posts = 0

    start_time = time.time()
    logger.info('Starting migration')

    if option_hipchat_amend_rooms:
        logger.info('Amending Hipchat room export')
        input_file = '%s/rooms.json' % migration_input_path
        amend_hipchat_rooms.amend_rooms(input_file, migration_output_path, OUTPUT_HC_ROOMS_AMENDED_FILENAME,
                                        option_hipchat_base_url, option_hipchat_tokens)
        logger.info('Amending room export finished')

    logger.info('Team migration started')
    mm_team = migrate_team()
    write_mm_json([mm_team], OUTPUT_TEAM_FILENAME)
    logger.info('Team migration finished')

    logger.info('User migration started')
    mm_users = migrate_users()
    stats_total_users = len(mm_users)
    mm_username_by_hc_id = dict([(u.get_hc_id(), u.username) for u in mm_users])
    logger.info('User migration finished')

    if option_migrate_direct_posts:
        logger.info('Direct post migration started')

        direct_channel_user_pairs = []
        for i, mm_user in enumerate(mm_users):
            logger.info('\tMigrating posts of user (username: %s) %d/%d' % (mm_user.username, i, len(mm_users)))
            mm_direct_posts_of_user = migrate_direct_posts(mm_username_by_hc_id, mm_user)
            stats_total_direct_posts += len(mm_direct_posts_of_user)
            logger.debug('\t\t%d posts migrated' % len(mm_direct_posts_of_user))
            write_mm_json(mm_direct_posts_of_user, '%s_%d' % (OUTPUT_DIRECT_POSTS_FILENAME, mm_user.get_hc_id()))
            direct_channel_user_pairs.extend(list(map(lambda p: frozenset(p.channel_members), mm_direct_posts_of_user)))

        mm_direct_channels = migrate_direct_channels(direct_channel_user_pairs)
        logger.debug('\t%d direct channels migrated' % len(mm_direct_channels))
        write_mm_json(mm_direct_channels, OUTPUT_DIRECT_CHANNELS_FILENAME)

        logger.info('Direct post migration finished')

    if option_migrate_channels:
        logger.info('Channel migration started')
        mm_channels = migrate_channels()
        stats_total_channels = len(mm_channels)
        logger.debug('\t%d channels migrated' % len(mm_channels))
        write_mm_json(mm_channels, OUTPUT_CHANNELS_FILENAME)

        if option_migrate_channel_posts:
            for i in range(len(mm_channels)):
                channel = mm_channels[i]
                logger.info('\tMigrating posts of channel (name: %s) %d/%d' % (channel.name, i, len(mm_channels)))
                mm_posts = migrate_channel_posts(mm_username_by_hc_id, channel)
                stats_total_channel_posts += len(mm_posts)
                logger.debug('\t\t%d posts migrated' % len(mm_posts))
                write_mm_json(mm_posts, '%s_%d' % (OUTPUT_CHANNEL_POSTS_FILENAME, channel.get_hc_id()))

                # Hipchat export does not include public room participants, Hipchat API only returns participant if user is online during the requests
                # As an educated guess if a user should become member of a public channel, we check if the user ever wrote a message in the room
                if option_public_membership_based_on_messages:
                    unique_senders = set(map(lambda p: p.get_user_hc_id(), mm_posts))
                    channel.add_channel_participants(unique_senders)

        # Another option (probably the most reliable one) to get participants of public Hipchat rooms, is to use a Redis export
        # redis_autojoin.sh produces the json file containing room memberships used here
        if option_public_membership_based_on_redis:
            participants_by_room_name = redis_participants_by_room_name()
            for c in mm_channels:
                c.add_channel_participants(participants_by_room_name.get(c.get_hc_name(), []))

        for mm_user in mm_users:
            channel_memberships = migrate_user_channel_membership(mm_channels, mm_user)
            if len(channel_memberships) > MM_MAX_CHANNEL_MEMBERSHIPS_PER_USER:
                logger.warning(
                    "Encountered user (username: %s) with too many channel memberships (%d of %d allowed). Skipping channel memberships!"
                    % (mm_user.username, len(channel_memberships), MM_MAX_CHANNEL_MEMBERSHIPS_PER_USER))
            mm_user.teams[0].channels = channel_memberships[0:MM_MAX_CHANNEL_MEMBERSHIPS_PER_USER - 1]

        logger.info('Channel migration finished')

    # Users need to be written after all other migrations, as other migrations have an impact (e.g. channels for the membership)
    write_mm_json(mm_users, OUTPUT_USERS_FILENAME)

    if option_migrate_hipchat_custom_emoticons or option_migrate_hipchat_builtin_emoticons:
        logger.info('Emoticon migration started')
        migrate_hipchat_emoticons.migrate_emoticons(migration_output_path, option_hipchat_base_url,
                                                    option_hipchat_tokens, option_migrate_hipchat_builtin_emoticons)
        logger.info('Emoticon migration finished')

    if option_concat_import_files:
        logger.info('Concat all migration files into %s.jsonl' % OUTPUT_ALL_IN_ONE_FILENAME)

        input_files = [full_output_path(OUTPUT_TEAM_FILENAME)]

        if option_migrate_hipchat_builtin_emoticons or option_migrate_hipchat_custom_emoticons:
            input_files.append(full_output_path(OUTPUT_EMOJI_FILENAME))

        if option_migrate_channels:
            input_files.append(full_output_path(OUTPUT_CHANNELS_FILENAME))

        input_files.append(full_output_path(OUTPUT_USERS_FILENAME))

        if option_migrate_direct_posts:
            input_files.append(full_output_path(OUTPUT_DIRECT_CHANNELS_FILENAME))
            direct_post_files = glob.glob('%s/%s*.jsonl' % (migration_output_path, OUTPUT_DIRECT_POSTS_FILENAME))
            input_files.extend(direct_post_files)

        if option_migrate_channels:
            channel_posts_files = glob.glob('%s/%s*.jsonl' % (migration_output_path, OUTPUT_CHANNEL_POSTS_FILENAME))
            input_files.extend(channel_posts_files)

        concat_files(input_files, OUTPUT_ALL_IN_ONE_FILENAME)

    logger.info("Migration finished")
    end_time = time.time()
    elapsed_time_minutes, elapsed_time_seconds = divmod(end_time - start_time, 60)
    logger.info('''
        Time elapsed: %d:%d
        Total users migrated: %d
        Total direct posts migrated: %d
        Total channels migrated: %d
        Total channel posts migrated: %d''' % (elapsed_time_minutes, elapsed_time_seconds,
                                               stats_total_users,
                                               stats_total_direct_posts,
                                               stats_total_channels,
                                               stats_total_channel_posts))

    import_help_text = '''
        mode=validate # or 'apply' once validation is successful
        mattermost_path=/opt/mattermost/bin # fix to point to your installation
    '''
    if option_concat_import_files:
        logger.info('''
            To import run the following comands:
            %s
            $mattermost_path/mattermost import bulk %s.jsonl --$mode
        ''' % (import_help_text, OUTPUT_ALL_IN_ONE_FILENAME))
    else:
        logger.info('''
                To import run the following commands in this order (skip the ones you did not migrate):
                %s
                $mattermost_path/mattermost import bulk %s.jsonl --$mode
                $mattermost_path/mattermost import bulk %s.jsonl --$mode
                $mattermost_path/mattermost import bulk %s.jsonl --$mode
                $mattermost_path/mattermost import bulk %s.jsonl --$mode
                $mattermost_path/mattermost import bulk %s.jsonl --$mode

                for filename in ./%s_*.jsonl; do
                  echo "Importing: $filename"
                  $mattermost_path/mattermost import bulk $filename --$mode
                done

                for filename in ./%s_*.jsonl; do
                  echo "Importing: $filename"
                  $mattermost_path/mattermost import bulk $filename --$mode
                done
        ''' % (
            import_help_text, OUTPUT_EMOJI_FILENAME, OUTPUT_TEAM_FILENAME, OUTPUT_CHANNELS_FILENAME,
            OUTPUT_USERS_FILENAME,
            OUTPUT_DIRECT_CHANNELS_FILENAME, OUTPUT_DIRECT_POSTS_FILENAME, OUTPUT_CHANNEL_POSTS_FILENAME))

    deleted_users_full_path = full_output_path(OUTPUT_DELETED_USERS_FILENAME, 'txt')
    if os.path.exists(deleted_users_full_path):
        logger.info('''
            Some deleted Hipchat users have been migrated. To deactivate them in Mattermost run:
            cat %s | xargs ./mattermost user deactivate
        ''' % (deleted_users_full_path))

    archived_channels_full_path = full_output_path(OUTPUT_ARCHIVED_CHANNELS_FILENAME, 'txt')
    if (os.path.exists(archived_channels_full_path)):
        logger.info('''
            Some archive Hipchat rooms have been migrated. To archive them in Mattermost run:
            cat %s | xargs ./mattermost channel archive
        ''' % (archived_channels_full_path))

    logger.info('''
        Run mark_as_read.py after the import has finished in order to mark all imported posts as read.
    ''')


if __name__ == "__main__":
    main()
