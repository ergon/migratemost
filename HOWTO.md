# How to migrate from Hipchat to Mattermost using Migratemost

## Prerequisites
- Mattermost installation (see [Installing Mattermost](https://docs.mattermost.com/guides/administrator.html#installing-mattermost))
- A Hipchat export (see [Export data from Hipchat Data Center](https://confluence.atlassian.com/hipchatdc3/export-data-from-hipchat-data-center-913476832.html) for on-premise installations or [Migrate to Slack](https://www.atlassian.com/partnerships/slack/migration) for Hipchat Cloud). The export file needs to be decrypted and extracted.
- Read the [`Caveats` section of README.md.](./README.md#caveats)

## Step 1: Installation
Checkout this repository and install the requirements.
```
pip install -r requirements.txt
```

## Step 2: Convert the data
**Ensure there is enough free disk space for the conversion**

Given you have the Hipchat data decrypted and extracted, this step will convert it for importation to Mattermost. If you want to use the features fetching missing data from Hipchat to amend the export (e.g. public room memberships, emoticons, avatars) you will also need Hipchat API tokens with "View Room" and "View Group" scope. Due to the 100/requests/5mins throttling of Hipchat, you may want to create several tokens in order to speed up the API calls done by Migratemost.

Run `migratemost.py` with the appropriate options as described [in the `Usage` section of README.md.](./README.md#usage)

### Example
`./migratemost.py -t MyTeam -o ./mm_data/ -i ./data/ -v --migrate-all --public-channel-membership-based-on-redis-export --apply-admin-team-role --map-town-square-channel="MyTeamRoom" --authentication-service=ldap --authentication-data-field=username --concat-output --filter-users=".*@mycompany.ch" --amend-rooms --migrate-custom-emoticons --hipchat-base-url=https://hipchat.mycompany.com/v2/ --hipchat-access-tokens=token1,token2,token3`

## Step 3: Import the data to Mattermost
Migratemost logs some help on how to import the data once it's done. Further help can be found at [Mattermost Bulk Loding](https://docs.mattermost.com/deployment/bulk-loading.html)

### Example
Given the `--concat-output` option:
```
mode=validate # or 'apply' once validation is successful
mattermost_path=/opt/mattermost/bin # fix to point to your installation
$mattermost_path/mattermost import bulk mm_all_data.jsonl --$mode
```

## Step 4: Fixup and cleanup
Some steps cannot be done using the bulk importer and therefore have to be executed manually. Some of them are using the [Mattermost CLI](https://docs.mattermost.com/administration/command-line-tools.html)

### Fix access rights
Required if the import is not run as mattermost service user (The bulk importert writes the attachment and avatar files as the user which runs the import).
```
chown -R <mattermost_user>:<mattermost_group> <mattermost data directory>
```

### Deactivate users that were deleted on Hipchat
This is only required if the option `--skip-archived-rooms` was not used.
```
cat <path to migration data>/users_to_deactivate.txt | xargs <mattermost installation>/bin/mattermost user deactivate
```

### Archive channels that were archived in Hipchat
```
cat <path to migration data>/channels_to_archive.txt.txt | xargs <mattermost installation>/bin/mattermost channel archive
```

### Mark all messages for all users as read
This prevents users to have thousands of unread messages and having the Mattermost client take a very long time to load.
```
./mark_as_read.py -b https://<URL of your Mattermost Server>/api/v4 -t <team of which message should be marked as read> -a <Mattermost API token with Admin rights>
```

Example: `./mark_as_read.py -b https://mattermost.mycompany.ch/api/v4 -t myteam -a sometoken`

### Restart Mattermost in order to fix online status of users after executing `mark_as_read.py` script
`service mattermost restart` (depending on your installation type)

## Step 5: Verify
Verify the data has been properly imported.
