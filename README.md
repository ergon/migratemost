# Migratemost
Migrate Hipchat data to Mattermost.

Migratemost is a set of Python scripts to migrate your Hipchat data to Mattermost. It uses the [Hipchat export](https://confluence.atlassian.com/hipchatdc3/export-and-import-data-from-hipchat-data-center-909770932.html) and optionally fetches additional data from Hipchat via REST API to amend the export. Based on that data, it generates JSONL files for the [Mattermost Bulk Loading](https://docs.mattermost.com/deployment/bulk-loading.html).

## Features
- Migrates users incl. their avatar
- Migrates Hipchat rooms and their chat history
- Migrates 1:1 chats
- Migrates user's roles and memberships
- Migrates attachments
- Migrates emoticons
- Migration can be executed partially (handy for debugging)
- Map your Hipchat team room to Mattermost's equivalent "Town Square"
- Filter users which shall be migrated by e-mail address
- Configurable team, authentication method, ...

## Requirements
Python 2.7

## Installation
```
pip install -r requirements.txt
```

## Getting Started
See [HOWTO.md](./HOWTO.md) for a detailed guide.

## Usage
```
Usage: migratemost.py [options]
        Converts a Hipchat export to Mattermost bulk import files.
        By default, only the team and users are migrated, for more options see "Migration Options"

Options:
  -h, --help            show this help message and exit
  -t DEFAULT_TEAM_DISPLAY_NAME, --team=DEFAULT_TEAM_DISPLAY_NAME
                        Default team name to which all users are assigned
  -o OUTPUT_PATH, --output-path=OUTPUT_PATH
                        Output path where migration files will be placed.
                        Defaults to current directory.
  -i INPUT_PATH, --input-path=INPUT_PATH
                        Path to Hipchat export (the 'data' directory of the
                        extracted export. Defaults to current directory.)
  -v, --verbose         Enable verbose logging
  --concat-output       Concatenate all output files into one after conversion
                        is done. Mattermost bulk import seems to be much
                        faster with one large files instead of many smaller
                        ones.

  Migration Options:
    These options control what data should be migrated

    --migrate-all       Use to migrate everything (recommended)
    --migrate-direct-posts
                        Use to migrate direct posts (1:1 in Hipchat)
    --migrate-channels  Use to migrate channels without the posts (rooms in
                        Hipchat)
    --migrate-channel-posts
                        Use to migrate channels including the posts (rooms and
                        messages in Hipchat)
    --migrate-avatars   Use to migrate users avatars
    --skip-archived-rooms
                        Use to to not migrate rooms that are marked as
                        archived in Hipchat
    --disable-tutorial  Use to to disable introductory tutorial of Mattermost
                        upon first logon for all users
    --shrink-image-to-limit
                        Shrink images to their maximum size allowed by
                        Mattermost
    --generate-email-addresses
                        Autogenerate fake e-mail addresses if they are null
                        (e.g. on guest accounts)
    --email-domain=EMAIL_DOMAIN
                        E-mail domain name used when generating fake e-mail
                        addresses. Addresses will be in the format:
                        mentionname@<email-domain>
    --public-channel-membership-based-on-hipchat-export
                        Use to have users join public channels if they were
                        member of the corresponding room in Hipchat. Room
                        membership is evaluated based on Hipchat export and
                        can be amended using the "--amend-rooms" option.
                        DISCLAIMER: Getting reliable public room memberships
                        out of Hipchat is not easy. See README.md for more
                        details.
    --public-channel-membership-based-on-messages
                        Use to have users join public channels if they were
                        member of the corresponding room in Hipchat. Room
                        membership is based on if a user has ever written a
                        message in the room. DISCLAIMER: Getting reliable
                        public room memberships out of Hipchat is not easy.
                        See README.md for more details.
    --public-channel-membership-based-on-redis-export
                        Use to have users join public channels if they were
                        member of the corresponding room in Hipchat. Room
                        membership is evaluated using a Redis export. Requires
                        the output of the "redis_autjoin.sh" script to be at
                        the input path. DISCLAIMER: Getting reliable public
                        room memberships out of Hipchat is not easy. See
                        README.md for more details.
    --apply-admin-team-role
                        Use to give users team admin role in Mattermost if
                        they had Hipchat admin rights.
    --apply-admin-system-role
                        Use to give users system admin role in Mattermost if
                        they had Hipchat admin rights.
    --map-town-square-channel=TOWN_SQUARE_SOURCE_ROOM_NAME
                        Map a Hipchat room to the town-square channel (default
                        channel)
    --filter-users=FILTER_USERS
                        Filter Hipchat users by e-mail address using regex
                        (important: filtered users must not occur in chat
                        history)

  Authentication Options:
    These options control what authentication settings should be applied
    to the migrated users.

    --authentication-service=AUTHENTICATION_SERVICE
                        Which authentication type to use, defaults to password
                        (Mattermost built-in authentication). If provided,
                        must be one of: gitlab, ldap, saml, google, office365
    --authentication-data-field=AUTHENTICATION_DATA_FIELD
                        Which user field to use for authentication service,
                        only relevant if other than Mattermost built-in
                        service is used. Valid choices are: username, email

  Hipchat Export Options:
    These options control data which will be fetched from Hipchat to amend
    the export

    --amend-rooms       Use to amend rooms exported by Hipchat with proper
                        participant and member lists (recommended)
    --migrate-custom-emoticons
                        Use to migrate custom Hipchat emoticons
    --migrate-builtin-emoticons
                        Use to migrate Hipchat built-in emoticons
    --hipchat-base-url=HIPCHAT_BASE_URL
                        Base URL of the Hipchat API, e.g.
                        https://hipchat.mycompany.ch/v2/
    --hipchat-access-tokens=HIPCHAT_TOKEN_LIST
                        Comma-separated list of access option_tokens with
                        "View Room" and "View Group" scope. Providing many
                        option_tokens speeds up the the API calls, as Hipchat
                        has a hardcoded 100 requests per token per 5 minutes
                        rate limit.
```

## Caveats
- Long messages (over 16383 characters) are not supported by Mattermost and are split into several posts
- Images with more than 24385536 pixels are [not accepted by the Mattermost bulk loader](https://github.com/mattermost/mattermost-server/blob/cee1e3685968cbf84b8b655bf438fb6d34a612e5/app/file.go#L696) By default such images are skipped. Using `--shrink-image-to-limit` images will be resized to match Mattermost's limits. 
- Attachments which cannot be found at the given path are skipped
- Hipchat private rooms are migrated to Mattermost private channels (not direct channels)
- Private channel members are migrated by default, as otherwise the users do not have access anymore (Mattermost bulk loader does not distinguish between members and participants)
- Public channel owners are migrated by default
- Public channel members are not migrated by default, but can be enabled by command-line switch
- Hipchat export contains only admins and owners of public rooms, but not participants. If it is acceptable for your users to manually join public channels after the migration, stop reading here ;) Otherwise, there is the following options to migrate members of public channels:
  - `--public-channel-membership-based-on-messages` Guess channel membership by looking at the messages a user has written. If a user has ever written a message in a public room, it will be added as a member to the corresponding public channel.

    Drawbacks are:
    - Users join channels, just because they wrote a message at some point but left the room a long time ago (could add some more factors, like max message age, but will still be unreliable)
    - Rooms that have the nature of an activity stream, where few users write and many read, will not be respected
  - `--public-channel-membership-based-on-hipchat-export` in combination with `--amend-rooms` Find memberships by amending the Hipchat export with data queried from the Hipchat REST API. The rooms.json export file will be extended with the participants returned from the REST calls.

    Drawbacks are:
    - REST API only returns participants, that are currently online. Users that are offline while running the migration have no memberships.
  - `--public-channel-membership-based-on-redis-export` Use auto-join information as it is stored in Redis of the Hipchat installation. This is probably the most reliable way of finding room memberships.

    Drawbacks are:
    - Hassle to fetch the Redis export manually from the Hipchat installation (but probably worth it)
- Output can be concatenated into one huge JSONL file. Might be easier to import and is faster in my experience (no overhead to startup the Mattermost process for every file).
- Migratemost has only been tested with Hipchat Data Center but should also work with Hipchat Cloud exports

## Troubleshooting
Open an issue or join the [Migratemost channel](https://pre-release.mattermost.com/core/channels/migratemost) on the Mattermost community server.

## Contributing
Bug reports and pull requests are welcome.

## License
[![License](http://img.shields.io/:license-mit-blue.svg?style=flat-square)](http://badges.mit-license.org)
The project is available as open source under the terms of the [MIT License](./LICENSE).

## Acknowledgements
- Thanks to [anx-ag](https://github.com/anx-ag) for a improvements and feedback
- Thanks to [Hipmost](https://github.com/orbitalimpact/hipmost) for a starting point
- Thanks to [Swood](https://github.com/swood) for further inspiration on issues like image size restrictions
- Thanks to [dra](https://github.com/dra) for implementing the image resizing
