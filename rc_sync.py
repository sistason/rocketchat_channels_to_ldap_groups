#!/bin/python3
from rocketchat_API.rocketchat import RocketChat
from requests.sessions import Session
import ldap3
import yaml
import logging
import os
import sys


logger = logging.getLogger(__name__)


class RCLDAPSync:

    def __init__(self, config_path='', rc_username="", rc_password="", rc_host="http://rocketchat:3000",
                 ldap_binduser="", ldap_password="", ldap_host="ldap://ldap:389", ldap_group_basedn='',
                 ldap_users_basedn='', ldap_group_objectclasses=None, ldap_users_objectclasses=None, channels=None):
        logging.basicConfig(level=logging.DEBUG)

        config_path = os.environ.get('CONFIG_PATH', config_path)
        if config_path:
            self._read_config(config_path)
        else:
            self.rc_username = os.environ.get('RC_USERNAME', rc_username)
            self.rc_password = os.environ.get('RC_PASSWORD', rc_password)
            self.rc_host = os.environ.get('RC_HOST', rc_host)

            self.ldap_binddn = os.environ.get('LDAP_BINDDN', ldap_binduser)
            self.ldap_password = os.environ.get('LDAP_PASSWORD', ldap_password)
            self.ldap_host = os.environ.get('LDAP_HOST', ldap_host)
            self.ldap_group_basedn = os.environ.get('LDAP_GROUP_BASEDN', ldap_group_basedn)
            self.ldap_users_basedn = os.environ.get('LDAP_USERS_BASEDN', ldap_users_basedn)
            self.ldap_users_objectclasses = os.environ.get['LDAP_USERS_OBJECTCLASSES', ldap_users_objectclasses]
            self.ldap_group_objectclasses = os.environ.get['LDAP_GROUP_OBJECTCLASSES', ldap_group_objectclasses]

            self.channels_to_sync = channels if channels is not None and type(dict) is list else {}

        self.session = Session()
        self.rocket = RocketChat(self.rc_username, self.rc_password, server_url=self.rc_host, session=self.session)
        self.ldap_server = ldap3.Server(self.ldap_host, get_info=ldap3.ALL)
        self.ldap_connection = ldap3.Connection(self.ldap_server, user=self.ldap_binddn, password=self.ldap_password)
        if not self.ldap_connection.bind():
            logger.error('Could not bind to LDAP! Invalid credentials? Wrong host?')

    def _read_config(self, config_path):
        with open(config_path, 'r') as stream:
            try:
                config = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                logger.error(exc)
                sys.exit(1)

        self.rc_username = config['RC_USERNAME']
        self.rc_password = config['RC_PASSWORD']
        self.rc_host = config.get("RC_HOST", "http://rocketchat:3000")

        self.ldap_binddn = config['LDAP_BINDDN']
        self.ldap_password = config['LDAP_PASSWORD']
        self.ldap_host = config.get('LDAP_HOST', "ldap://ldap:389")
        self.ldap_group_basedn = config['LDAP_GROUP_BASEDN']
        self.ldap_users_basedn = config['LDAP_USERS_BASEDN']
        self.ldap_users_objectclasses = config['LDAP_USERS_OBJECTCLASSES']
        self.ldap_group_objectclasses = config['LDAP_GROUP_OBJECTCLASSES']

        self.channels_to_sync = config['CHANNELS_TO_SYNC']

    def _get_ldap_group_member_uids(self, group_dn):
        self.ldap_connection.search(group_dn, '(objectClass=*)',
                                    attributes=['member', 'memberUid'])
        if not self.ldap_connection.response:
            # Group does not exist
            return None

        attrs = self.ldap_connection.response[0].get('attributes', {})
        return [i.split('=', 1)[1].split(',')[0] for i in attrs['member']] + attrs['memberUid']

    def sync_channels_rc_to_ldap(self):
        for rc_channel, ldap_group in self.channels_to_sync.items():
            logger.info(f'Adding RC channel "#{rc_channel}" to LDAP group "{ldap_group}"...')

            ldap_group_dn = f"{ldap_group},{self.ldap_group_basedn}"
            ldap_group_members = self._get_ldap_group_member_uids(ldap_group_dn)
            if ldap_group_members is None:
                ldap_group_cn = ldap_group.split('=', 1)[1].split(',')[0]
                self.ldap_connection.add(ldap_group_dn, object_class=self.ldap_group_objectclasses,
                                         attributes={'cn': ldap_group_cn, 'members': []})   #TODO: check if empty members conforms

            rc_channel_members = self.rocket.channels_members(channel=rc_channel).json().get('members')

            logger.debug(f'  RC channel members: {[i["username"] for i in rc_channel_members]}')
            logger.debug(f'  LDAP group members: {ldap_group_members}')

            for rc_member in rc_channel_members:
                rc_username = rc_member.get('username')
                if rc_username not in ldap_group_members:
                    ldap_username = f"uid={rc_username},{self.ldap_users_basedn}"
                    self.ldap_connection.modify(f"{ldap_group},{self.ldap_group_basedn}",
                                                {'member': [(ldap3.MODIFY_ADD, [ldap_username])]})
                    logger.info(f'    Added RC user {rc_username}')

        self.close()

    def sync_groups_ldap_to_rc(self):
        for rc_channel, ldap_group in self.channels_to_sync.items():
            logger.info(f'Adding LDAP-Group "{ldap_group}" to RC channel "{rc_channel}"...')

            _channel = self.rocket.channels_info(channel=rc_channel).json().get('channel')
            rc_channel_id = _channel.get('_id')
            rc_channel_members = self.rocket.channels_members(channel=rc_channel).json().get('members')
            ldap_group_members = self._get_ldap_group_member_uids(ldap_group)
            logger.debug(f'  LDAP group members: {ldap_group_members}')
            logger.debug(f'  RC channel members: {[i["username"] for i in rc_channel_members]}')

            for member in ldap_group_members:
                if member not in rc_channel_members:
                    user_id = self.rocket.users_info(username=member).json().get('user', {}).get('_id')
                    if self.rocket.channels_invite(user_id=user_id, room_id=rc_channel_id).json().get('success'):
                        logger.info(f'    Added LDAP user "{member}" to RC channel "#{rc_channel}"')
                    else:
                        logger.info(f'    ! LDAP user "{member}" could not be added, has no RC-account yet')

        self.close()

    def _get_ldap_users(self):
        self.ldap_connection.search(self.ldap_users_basedn,
                                    f'(&{"".join([f"(objectClass={obc})" for obc in self.ldap_users_objectclasses])})',
                                    attributes=ldap3.ALL_ATTRIBUTES)
        return dict([(user.get('dn'), user) for user in self.ldap_connection.response])

    def sync_users_rc_to_ldap(self):
        all_rc_users = self.rocket.users_list().json().get('users', [])
        all_ldap_users = self._get_ldap_users()

        # Add all from Rocket.Chat to LDAP
        for user in all_rc_users:
            if user.get('type') == 'bot':
                continue

            _api = self.rocket.users_info(user_id=user.get('_id'))
            if _api.status_code == 404:
                # Yes, some user are not gettable by id...
                _api = self.rocket.users_info(username=user.get('username'))
            _api = _api.json()
            if not _api.get('success'):
                logger.error(f'Could not get user info because "{_api.get("error")}"')
                break
            user_full = _api.get('user')

            user_password = user_full.get('services', {}).get('password', {}).get('bcrypt')

            if not user_password:
                # No local RC user
                continue

            uid = user.get('username')
            dn = f'uid={uid},{self.ldap_users_basedn}'
            ldap_password = "{SHA256-BCRYPT}"+user_password
            mail = user_full.get('emails', [{}])[0].get('address', None)
            cn = user_full.get('name')
            avatar = self.rocket.users_get_avatar(user_id=user.get('_id')).content

            # print(len(avatar))

            # avatar = avatar.decode('utf-8')
            # print(len(avatar))
            # from ldap3.utils import conv
            # from io import BytesIO
            # from base64 import b64decode, b64encode

            # avatar = conv.escape_filter_chars(avatar, 'UTF-8')

            #TODO: always image or sometimes link?
            #TODO: get into LDAP, the "right" way :)
            #avatar = self.session.get(avatar_url).content

            logger.debug(f'uid:{uid} - cn:{cn} - pw:{"{BCRYPT}" if user_password.startswith("$2b$") else user_password[:10]}')

            if dn not in all_ldap_users.keys():
                # Create LDAP Entry
                self.ldap_connection.add(dn, object_class=self.ldap_users_objectclasses,
                                         attributes={'cn': cn, 'mail': mail, 'uid': uid,
                                                     'userPassword': ldap_password,
                                                     'thumbnailPhoto': avatar,
                                                     'jpegPhoto': avatar})
                logger.info(f'    Created RC user "{uid}" in LDAP')
            else:
                # Update LDAP Entry
                self.ldap_connection.modify(dn, {'cn': [(ldap3.MODIFY_REPLACE, [cn])],
                                                 'mail': [(ldap3.MODIFY_REPLACE, [mail])],
                                                 'uid': [(ldap3.MODIFY_REPLACE, [uid])],
                                                 'userPassword': [(ldap3.MODIFY_REPLACE, [ldap_password])],
                                                 'thumbnailPhoto': [(ldap3.MODIFY_REPLACE, avatar)],
                                                 'jpegPhoto': [(ldap3.MODIFY_REPLACE, avatar)]})
                logger.info(f'    Updated RC user "{uid}" in LDAP')



        # Delete all the LDAP users not in Rocket.Chat
        rc_uids = [user.get('username') for user in all_rc_users]
        remaining = [user for user in all_ldap_users.values()
                     if user.get('attributes', {}).get('uid', [''])[0] not in rc_uids]

        for ldap_user in remaining:
            self.ldap_connection.delete(ldap_user.get('dn'))
            result = self.ldap_connection.result
            if result and not result.get('result', -1):
                logger.error(f'Could not delete {ldap_user.get("dn")}:\n\t"{result.get("message")}"!')
            else:
                logger.info(f'Deleted from LDAP: {ldap_user.get("attributes", {}).get("uid", [""])[0]}')

        self.close()

    def close(self):
        self.session.close()


def parse_args():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--verbose', action="store_true")
    parser.add_argument('-q', '--quiet', action="store_true")
    parser.add_argument('actions', nargs='+')

    _args = parser.parse_args()
    if _args.verbose:
        logger.setLevel(logging.DEBUG)
    elif _args.quiet:
        logger.setLevel(logging.ERROR)

    return _args


if __name__ == '__main__':
    args = parse_args()
    sync = RCLDAPSync()
    # preserve the order
    for action in args.actions:
        if 'sync_channels_rc_to_ldap' == action:
            sync.sync_channels_rc_to_ldap()
        if 'sync_users_rc_to_ldap' == action:
            sync.sync_users_rc_to_ldap()
        if 'sync_groups_ldap_to_rc' == action:
            sync.sync_groups_ldap_to_rc()
