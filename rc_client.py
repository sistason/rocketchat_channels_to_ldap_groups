from requests import Session
from rocketchat_API.rocketchat import RocketChat
import logging

logger = logging.getLogger(__name__)


class RocketChatClient:
    def __init__(self, rc_username="", rc_password="", rc_host="http://rocketchat:3000", rc_ignore_users=None,
                 log_level=logging.INFO):
        self.rc_username = rc_username
        self.rc_password = rc_password
        self.rc_host = rc_host
        self.rc_ignore_users = rc_ignore_users
        logger.setLevel(log_level)

        self.session = Session()
        self.rocket = RocketChat(rc_username, rc_password, server_url=rc_host, session=self.session)

    def get_channel_id(self, rc_channel):
        _channel = self.rocket.channels_info(channel=rc_channel)
        if not _channel.ok:
            logger.info(f'Channel "#{rc_channel}" in the config is not found on the Rocket.Chat instance! '
                        f'Misconfiguration? Channel renamed?')
            return
        _channel = _channel.json().get('channel')
        return _channel.get('_id')

    def should_be_skipped(self, user_full):
        return ('bot' in user_full.get('roles') or
                user_full.get('username') in self.rc_ignore_users or
                not user_full.get('services', {}).get('password', {}).get('bcrypt'))

    def get_group_members_admin_workaround(self, rc_channelname):
        # https://github.com/RocketChat/Rocket.Chat/issues/15435
        all_groups = self.rocket.groups_list_all().json().get('groups', [])
        groups = list(filter(lambda g: g.get('name') == rc_channelname, all_groups))
        if not groups:
            return []

        group = groups[0]
        me = self.rocket.me().json()
        if not self.rocket.groups_invite(room_id=group.get('_id'), user_id=me.get('_id')).json().get('success'):
            return []

        members_ret = self.rocket.groups_members(room_id=group.get('_id')).json()
        if not members_ret.get('success'):
            return []

        return members_ret.get('members')

    def get_user_details(self, user):
        _api = self.rocket.users_info(user_id=user.get('_id'))
        if _api.status_code == 404:
            # Yes, some user are not gettable by id...
            _api = self.rocket.users_info(username=user.get('username'))
        _api = _api.json()
        if not _api.get('success'):
            logger.error(f'Could not get user info because "{_api.get("error")}"')
            return
        return _api.get('user')

    def get_user_avatar(self, user):
        avatar = self.rocket.users_get_avatar(user_id=user.get('_id'))
        if avatar.status_code == 404:
            avatar = self.rocket.users_get_avatar(username=user.get('username'))

        #            with open('/home/sistason/kai_rc.jpeg', 'wb') as f:
        #                f.write(avatar)

        # print(len(avatar))

        # avatar = avatar.decode('utf-8')
        # print(len(avatar))
        # from ldap3.utils import conv
        # from io import BytesIO
        # from base64 import b64decode, b64encode

        # avatar = conv.escape_filter_chars(avatar, 'UTF-8')

        # TODO: always image or sometimes link?
        # TODO: get into LDAP, the "right" way :)
        # avatar = self.session.get(avatar_url).content

        return avatar.content

    def get_all_users(self):
        all_users = []
        users_call = self.rocket.users_list().json()
        all_users.extend(users_call.get('users', []))

        while users_call.get('total') > users_call.get('count') + users_call.get('offset', 0):
            users_call = self.rocket.users_list(offset=int(users_call.get('count'))).json()
            all_users.extend(users_call.get('users', []))

        return [self.get_user_details(user) for user in all_users]

    def add_user_to_channel(self, username, rc_channel):
        rc_channel_id = self.get_channel_id(rc_channel)

        user_id = self.rocket.users_info(username=username).json().get('user', {}).get('_id')
        return self.rocket.channels_invite(user_id=user_id, room_id=rc_channel_id).json().get('success')

    def get_rc_channel_members(self, rc_channel):
        channel_info = self.rocket.channels_info(channel=rc_channel).json()
        if channel_info.get('success'):
            room_id = channel_info.get('channel').get('_id')
            return self.rocket.channels_members(room_id=room_id).json().get('members')
        else:
            logger.debug(f' Channel "#{rc_channel}" is probably private, so checking groups...')
            group_info = self.rocket.groups_info(room_name=rc_channel).json()
            if group_info.get('success'):
                room_id = group_info.get('group').get('_id')
                return self.rocket.groups_members(room_id=room_id).json().get('members')
            else:
                logger.debug(f' No member of group "#{rc_channel}", or does not exist. trying to become member...')
                return self.get_group_members_admin_workaround(rc_channel)

