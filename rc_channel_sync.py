#!/bin/python3
from rocketchat_API.rocketchat import RocketChat
from requests.sessions import Session
import ldap3
import yaml
import logging
import os
import sys


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class RCLDAPSync:

    def __init__(self, config_path='', rc_username="", rc_password="", rc_host="http://rocketchat:3000",
                 ldap_binduser="", ldap_password="", ldap_host="ldap://ldap:389", ldap_group_basedn='',
                 ldap_users_basedn='', channels=None):
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

        self.channels_to_sync = config['CHANNELS_TO_SYNC']

    def _get_ldap_group_member_uids(self, group):
        self.ldap_connection.search(group + ',' + self.ldap_group_basedn, '(objectClass=*)',
                                    attributes=['member', 'memberUid'])
        attrs = self.ldap_connection.response[0].get('attributes', {})
        return [i.split('=', 1)[1].split(',')[0] for i in attrs['member']] + attrs['memberUid']

    def sync_rc_to_ldap(self):
        for rc_channel, ldap_group in self.channels_to_sync.items():
            logger.info(f'Adding RC channel "#{rc_channel}" to LDAP group "{ldap_group}"...')

            ldap_group_members = self._get_ldap_group_member_uids(ldap_group)
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

    def sync_ldap_to_rc(self):
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

    def close(self):
        self.session.close()


if __name__ == '__main__':
    if len(sys.argv) == 1 or sys.argv[1] == 'rc2ldap':
        sync = RCLDAPSync()
        sync.sync_rc_to_ldap()
    elif sys.argv[1] == 'ldap2rc':
        sync = RCLDAPSync()
        sync.sync_ldap_to_rc()
    else:
        print('Unknown argument: Either rc2ldap or ldap2rc!')
