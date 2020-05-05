#!/bin/python3
import ldap3
import yaml
import logging
import os
import sys

from rc_client import RocketChatClient
from ldap_client import LDAPClient

logger = logging.getLogger(__name__)
logging.getLogger('urllib3').setLevel(logging.INFO)


class RCLDAPSync:

    @staticmethod
    def from_env(channels, log_level=logging.INFO):
        if type(channels) is list:
            channels = {c.split('=', 1) for c in channels}
        return RCLDAPSync(
            RocketChatClient(
                rc_username=os.environ.get('RC_USERNAME'),
                rc_password=os.environ.get('RC_PASSWORD'),
                rc_host=os.environ.get('RC_HOST'),
                rc_ignore_users=os.environ.get('RC_IGNORE_USERS'),
                log_level=log_level
            ),
            LDAPClient(
                ldap_binddn=os.environ.get('LDAP_BINDDN'),
                ldap_password=os.environ.get('LDAP_PASSWORD'),
                ldap_host=os.environ.get('LDAP_HOST'),
                ldap_group_basedn=os.environ.get('LDAP_GROUP_BASEDN'),
                ldap_users_basedn=os.environ.get('LDAP_USERS_BASEDN'),
                ldap_group_objectclasses=os.environ.get('LDAP_GROUP_OBJECTCLASSES'),
                ldap_users_objectclasses=os.environ.get('LDAP_USERS_OBJECTCLASSES'),
                log_level=log_level
            ),
            channels=channels if channels is not None and type(channels) is dict else {}
        )

    @staticmethod
    def from_config(config_path, log_level=logging.INFO):
        with open(config_path, 'r') as stream:
            try:
                config = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                logger.error(exc)
                sys.exit(1)

        return RCLDAPSync(
            RocketChatClient(
                rc_username=config['RC_USERNAME'],
                rc_password=config['RC_PASSWORD'],
                rc_host=config.get("RC_HOST", "http://rocketchat:3000"),
                rc_ignore_users=config['RC_IGNORE_USERS'],
                log_level=log_level
            ),
            LDAPClient(
                ldap_binddn=config['LDAP_BINDDN'],
                ldap_password=config['LDAP_PASSWORD'],
                ldap_host=config.get('LDAP_HOST', "ldap://ldap:389"),
                ldap_group_basedn=config['LDAP_GROUP_BASEDN'],
                ldap_users_basedn=config['LDAP_USERS_BASEDN'],
                ldap_group_objectclasses=config['LDAP_GROUP_OBJECTCLASSES'],
                ldap_users_objectclasses=config['LDAP_USERS_OBJECTCLASSES'],
                log_level=log_level
            ),
            channels=config['CHANNELS_TO_SYNC']
        )

    def __init__(self, rc_client, ldap_client, channels=None):
        self.ldap_client = ldap_client

        self.rc_client = rc_client
        self.rc_ignore_users = rc_client.rc_ignore_users

        self.channels_to_sync = channels

    def sync_channels_rc_to_ldap(self):
        self.ldap_client.all_users = self.ldap_client.get_all_users()

        for rc_channel, ldap_group in self.channels_to_sync.items():
            logger.info(f'Adding RC channel "#{rc_channel}" to LDAP group "{ldap_group}"...')

            ldap_group_members = self.ldap_client.get_group_member_uids(group_name=ldap_group)
            rc_channel_members = self.rc_client.get_rc_channel_members(rc_channel)
            if rc_channel_members is None:
                logger.info(f'Channel "#{rc_channel}" in the config is not found on the Rocket.Chat instance! '
                            f'Misconfiguration? Channel/Group renamed?')
                continue

            logger.debug(f'  RC channel members: {[i["username"] for i in rc_channel_members]}')
            logger.debug(f'  LDAP group members: {ldap_group_members}')

            for rc_member in rc_channel_members:
                self.ldap_client.add_rc_user_to_ldap_group(ldap_group, ldap_group_members, rc_member.get('username'))

            self.ldap_client.delete_ldap_users_not_in_rc_channel(ldap_group, rc_channel_members)

    def sync_groups_ldap_to_rc(self):
        for rc_channel, ldap_group in self.channels_to_sync.items():
            logger.info(f'Adding LDAP-Group "{ldap_group}" to RC channel "{rc_channel}"...')

            rc_channel_members = self.rc_client.get_rc_channel_members(rc_channel)
            ldap_group_members = self.ldap_client.get_ldap_group_member_uids(ldap_group)

            logger.debug(f'  LDAP group members: {ldap_group_members}')
            logger.debug(f'  RC channel members: {[i["username"] for i in rc_channel_members]}')

            for member in ldap_group_members:
                if member not in rc_channel_members:
                    if self.rc_client.add_user_to_channel(member, rc_channel):
                        logger.info(f'    Added LDAP user "{member}" to RC channel "#{rc_channel}"')
                    else:
                        logger.info(f'    ! LDAP user "{member}" could not be added, has no RC-account yet')

    def sync_users_rc_to_ldap(self):
        all_rc_users = self.rc_client.get_all_users()
        rc_users_to_sync = filter(lambda user: not self.rc_client.should_be_skipped(user), all_rc_users)
        all_ldap_users = self.ldap_client.get_all_users()

        for user_full in rc_users_to_sync:
            dn, ldap_attributes = self._get_user_details(user_full)
            self.ldap_client.add_or_update_user(all_ldap_users, dn, ldap_attributes)

        self.ldap_client.delete_users_not_in_rc(all_ldap_users, all_rc_users)

    def _get_user_details(self, user_full):
        user_password = user_full.get('services', {}).get('password', {}).get('bcrypt')
        uid = user_full.get('username')
        dn = f'uid={uid},{self.ldap_client.ldap_users_basedn}'
        ldap_password = "{SHA256-BCRYPT}" + user_password
        mail = user_full.get('emails', [{}])[0].get('address', None)
        cn = user_full.get('name')
        avatar = self.rc_client.get_user_avatar(user_full)

        logger.debug(f'uid:{uid} - cn:{cn}')
        return (dn, {'cn': cn, 'mail': mail, 'uid': uid,
                     'userPassword': ldap_password,
                     'thumbnailPhoto': avatar,
                     'jpegPhoto': avatar})

    def close(self):
        self.rc_client.session.close()
        self.ldap_client.ldap_connection.unbind()


def parse_args():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--verbose', action="store_true")
    parser.add_argument('-q', '--quiet', action="store_true")
    parser.add_argument('--repeat_every_seconds', type=int)
    parser.add_argument('--config', type=str)
    parser.add_argument('--channel', nargs='*')
    parser.add_argument('actions', nargs='+')

    _args = parser.parse_args()
    return _args


def run_actions(sync_, actions):
    # preserve the order
    for action in actions:
        if 'sync_users_rc_to_ldap' == action:
            sync_.sync_users_rc_to_ldap()
        if 'sync_channels_rc_to_ldap' == action:
            sync_.sync_channels_rc_to_ldap()
        if 'sync_groups_ldap_to_rc' == action:
            sync_.sync_groups_ldap_to_rc()


if __name__ == '__main__':
    args = parse_args()

    if args.verbose:
        log_level = logging.DEBUG
    elif args.quiet:
        log_level = logging.ERROR
    else:
        log_level = logging.INFO
    logging.basicConfig(level=log_level)

    if args.config:
        sync = RCLDAPSync.from_config(args.config, log_level=log_level)
    else:
        sync = RCLDAPSync.from_env(args.channel, log_level=log_level)

    run_actions(sync, args.actions)
    if args.repeat_every_seconds:
        import time
        while not time.sleep(args.repeat_every_seconds):
            run_actions(sync, args.actions)

    sync.close()
