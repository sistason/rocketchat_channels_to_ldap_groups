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
    def from_env(sync_, loglevel=logging.INFO):
        return RCLDAPSync(
            RocketChatClient(
                username=os.environ.get('RC_USERNAME'),
                password=os.environ.get('RC_PASSWORD'),
                host=os.environ.get('RC_HOST'),
                ignore_users=os.environ.get('RC_IGNORE_USERS', []),
                custom_user_field=os.environ.get('RC_CUSTOM_USER_FIELD'),
                custom_user_field_conversions=os.environ.get('RC_CUSTOM_USER_FIELD_CONVERSIONS', {}),
                log_level=loglevel
            ),
            LDAPClient(
                binddn=os.environ.get('LDAP_BINDDN'),
                password=os.environ.get('LDAP_PASSWORD'),
                host=os.environ.get('LDAP_HOST'),
                base_dn=os.environ.get('LDAP_BASE_DN'),
                users_objectclasses=os.environ.get('LDAP_USERS_OBJECTCLASSES'),
                groups_objectclasses=os.environ.get('LDAP_GROUPS_OBJECTCLASSES'),
                log_level=loglevel
            ),
            sync=sync_ if sync_ is not None and type(sync_) is dict else {}
        )

    @staticmethod
    def from_config(config_path, loglevel=logging.INFO):
        with open(config_path, 'r') as stream:
            try:
                config = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                logger.error(exc)
                sys.exit(1)

        return RCLDAPSync(
            RocketChatClient(
                username=config.get('RC_USERNAME'),
                password=config.get('RC_PASSWORD'),
                host=config.get("RC_HOST"),
                ignore_users=config.get('RC_IGNORE_USERS'),
                custom_user_field=config.get('RC_CUSTOM_USER_FIELD'),
                custom_user_field_conversions=config.get('RC_CUSTOM_USER_FIELD_CONVERSIONS'),
                log_level=loglevel
            ),
            LDAPClient(
                binddn=config['LDAP_BINDDN'],
                password=config['LDAP_PASSWORD'],
                host=config.get('LDAP_HOST'),
                base_dn=config.get('LDAP_BASE_DN'),
                default_users_objectclasses=config.get('LDAP_DEFAULT_USERS_OBJECTCLASSES'),
                default_groups_objectclasses=config.get('LDAP_DEFAULT_GROUPS_OBJECTCLASSES'),
                default_users_basedn=config.get('LDAP_DEFAULT_USERS_BASEDN'),
                default_groups_basedn=config.get('LDAP_DEFAULT_GROUPS_BASEDN'),
                log_level=loglevel
            ),
            sync=config['SYNC']
        )

    def __init__(self, rc_client, ldap_client, sync=None):
        self.ldap_client = ldap_client

        self.rc_client = rc_client

        self.channels_to_sync = sync

    def sync_channels_rc_to_ldap(self):
        for name_, channel_settings in self.channels_to_sync.items():
            logger.debug(f"Syncing channels from {name_}...")

            self.ldap_client.update_settings(channel_settings)

            for rc_channel, ldap_group in channel_settings.get('channels').items():
                logger.info(f'Adding RC channel "#{rc_channel}" to LDAP group "{ldap_group},{channel_settings.get("groups_basedn")}"...')

                ldap_group_members = self.ldap_client.get_group_member_dns(ldap_group)
                if ldap_group_members is None:
                    logger.debug(f'LDAP Group "{ldap_group}" was missing, adding...')
                    if not self.ldap_client.add_group(ldap_group):
                        logger.error(f'Could not add LDAP Group "{ldap_group}"!')
                        return
                    ldap_group_members = []

                rc_channel_members = self.rc_client.get_rc_channel_members(rc_channel)
                if rc_channel_members is None:
                    logger.info(f'Channel "#{rc_channel}" in the config is not found on the Rocket.Chat instance! '
                                f'Misconfiguration? Channel/Group renamed?')
                    continue

                logger.debug(f'  RC channel members: {[i.get("username") for i in rc_channel_members]}')
                logger.debug(f'  LDAP group members: {ldap_group_members}')

                dn_to_have = []

                for rc_member in rc_channel_members:
                    rc_user = self.rc_client.get_rc_user(rc_member)
                    if self.rc_client.should_be_skipped(rc_user):
                        continue

                    ldap_user = self.ldap_client.get_user_by_rocketchat_id(rc_user.rocketchat_id)
                    if ldap_user is None:
                        dn = None
                        if self.rc_client.custom_user_field:
                            dn = self.rc_client.get_dn_of_rc_user_by_custom_field(rc_user)
                        if not dn:
                            dn = f'uid={rc_user.username},{channel_settings.get("users_basedn")}'

                        self.ldap_client.add_or_update_user(dn, self._get_ldap_dict(rc_user))
                    else:
                        dn = ldap_user.get('dn')

                    dn_to_have.append(dn)

                if dn_to_have == ldap_group_members:
                    # In sync, everything is the way we want it
                    continue

                self.ldap_client.set_group_members(ldap_group, dn_to_have)

    def sync_groups_ldap_to_rc(self):
        for base_dn, channel_settings in self.channels_to_sync.items():
            self.ldap_client.update_settings(channel_settings)
            for rc_channel, ldap_group in channel_settings.get('channels').items():
                logger.info(f'Adding LDAP-Group "{ldap_group},{channel_settings.get("groups_basedn")}" to RC channel "{rc_channel}"...')

                rc_channel_members = self.rc_client.get_rc_channel_members(rc_channel)
                if rc_channel_members is None:
                    logger.debug(f'Adding RC group {rc_channel}...')
                    if not self.rc_client.add_group(rc_channel):
                        logger.error(f'Could not add RC group {rc_channel}!')
                        return
                    rc_channel_members = []

                ldap_group_members = self.ldap_client.get_group_member_dns(ldap_group)
                if ldap_group_members is None:
                    logger.debug(f'LDAP Group "{ldap_group}" is missing, skipping...')
                    continue

                logger.debug(f'  LDAP group members: {ldap_group_members}')
                logger.debug(f'  RC channel members: {[i["username"] for i in rc_channel_members]}')

                rc_channel_member_uids = [i.get('uid') for i in rc_channel_members]
                for member_uid in [i.split('=', 1)[1].split(',')[0] for i in ldap_group_members]:
                    if member_uid not in rc_channel_member_uids:
                        rc_user = self.rc_client.get_rc_user(member_uid)
                        if self.rc_client.should_be_skipped(rc_user):
                            continue

                        if self.rc_client.add_userid_to_channel(rc_user.rocketchat_id, rc_channel):
                            logger.info(f'    Added LDAP user "{member_uid}" to RC channel "#{rc_channel}"')
                        else:
                            logger.info(f'    ! LDAP user "{member_uid}" could not be added, has no RC-account yet')

    def sync_users_rc_to_ldap(self):
        if self.rc_client.custom_user_field:
            # Since the custom user field sets the user_dn, generate all users up front
            self._add_users_rc_to_ldap_with_custom_field()
        else:
            # Iterate through all sync-groups and get user_dn from channels
            self._add_users_rc_to_ldap_with_channels()

        all_ldap_users = self.ldap_client.get_all_users(self.ldap_client.ldap_base_dn)
        for ldap_dn, ldap_user in all_ldap_users.items():
            ldap_uid = ldap_user.get('attributes', {}).get("uid")
            if type(ldap_uid) is list:
                ldap_uid = ldap_uid[0]
            if ldap_uid not in self.rc_client.known_rc_users:
                self.ldap_client.delete_dn(ldap_dn)

    def _add_users_rc_to_ldap_with_custom_field(self):
        all_rc_users = self.rc_client.get_all_users()
        for rc_user_info in all_rc_users:
            rc_user = self.rc_client.get_rc_user(rc_user_info)

            dn = self.rc_client.get_dn_of_rc_user_by_custom_field(rc_user)
            if dn:
                self.ldap_client.add_or_update_user(dn, self._get_ldap_dict(rc_user))

    def _add_users_rc_to_ldap_with_channels(self):
        users_added_cache = []
        for name_, channel_settings in self.channels_to_sync.items():
            logger.debug(f"Adding users from {name_}...")
            self.ldap_client.update_settings(channel_settings)
            for rc_channel, ldap_group in channel_settings.get('channels').items():
                rc_channel_members = self.rc_client.get_rc_channel_members(rc_channel)

                for user_simple in rc_channel_members:
                    rc_user = self.rc_client.get_rc_user(user_simple)
                    if not self.rc_client.should_be_skipped(rc_user) and rc_user.username not in users_added_cache:
                        self.ldap_client.add_or_update_user(f'uid={rc_user.username}', self._get_ldap_dict(rc_user))
                        users_added_cache.append(rc_user.username)

    def _get_ldap_dict(self, user):
        avatar = self.rc_client.get_user_avatar(user.raw)

        logger.debug(f'uid:{user.username} - cn:{user.name}')
        return {'cn': user.name, 'mail': user.mail, 'uid': user.username,
                'userPassword': "{SHA256-BCRYPT}" + user.password_hash,
                'thumbnailPhoto': avatar,
                'jpegPhoto': avatar,
                'rocketchatId': user.rocketchat_id}

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
    parser.add_argument('actions', nargs='+', choices=['sync_users_rc_to_ldap', 'sync_channels_rc_to_ldap',
                                                       'sync_groups_ldap_to_rc'])

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
        rcldap_sync = RCLDAPSync.from_config(args.config, loglevel=log_level)
    else:
        rcldap_sync = RCLDAPSync.from_env(args.channel, loglevel=log_level)

    run_actions(rcldap_sync, args.actions)
    if args.repeat_every_seconds:
        import time
        while not time.sleep(args.repeat_every_seconds):
            run_actions(rcldap_sync, args.actions)

    rcldap_sync.close()
