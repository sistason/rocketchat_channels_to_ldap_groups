from requests import Session
from rocketchat_API.rocketchat import RocketChat
import logging

logger = logging.getLogger(__name__)


class RCUser:
    def __init__(self, rc_full_details):
        self.raw = rc_full_details

        self.username = rc_full_details.get("username")
        self.rocketchat_id = rc_full_details.get("_id")
        self.name = rc_full_details.get("name")
        self.mail = rc_full_details.get('emails', [{}])[0].get('address', None)
        self.password_hash = rc_full_details.get('services', {}).get('password', {}).get('bcrypt')
        self.custom_fields = rc_full_details.get("customFields", {})
        self.roles = rc_full_details.get("roles", [])


class RocketChatClient:
    def __init__(self, username, password, host="http://rocketchat:3000", ignore_users=None, custom_user_field=None,
                 custom_user_field_conversions=None, log_level=logging.INFO):
        self.username = username
        self.password = password
        self.host = host
        self.custom_user_field = custom_user_field
        self.custom_user_field_conversions = custom_user_field_conversions if custom_user_field_conversions is not None else {}
        self.ignore_users = ignore_users if ignore_users is not None else []
        logger.setLevel(log_level)

        self.session = Session()
        self.rocket = RocketChat(self.username, self.password, server_url=self.host, session=self.session)

        self.known_rc_users = {}

    def get_channel_id(self, rc_channel):
        _channel = self.rocket.channels_info(channel=rc_channel)
        if _channel.json().get('success'):
            _channel = _channel.json().get('channel')
            return _channel.get('_id')
        else:
            logger.debug(f' Channel "#{rc_channel}" is probably private, so checking groups...')
            group_info = self.rocket.groups_info(room_name=rc_channel).json()
            if group_info.get('success'):
                return group_info.get('group').get('_id')

        logger.info(f'Channel "#{rc_channel}" in the config is not found on the Rocket.Chat instance! '
                    f'Misconfiguration? Channel renamed?')

    def should_be_skipped(self, rc_user):
        return (rc_user is None or
                'bot' in rc_user.roles or
                'app' in rc_user.roles or
                rc_user.username in self.ignore_users or
                not rc_user.password_hash)

    def get_group_members_admin_workaround(self, rc_channelname):
        # https://github.com/RocketChat/Rocket.Chat/issues/15435
        all_groups = self.rocket.groups_list_all().json().get('groups', [])
        groups = list(filter(lambda g: g.get('name') == rc_channelname, all_groups))
        if not groups:
            return None

        group = groups[0]
        me = self.rocket.me().json()
        if not self.rocket.groups_invite(room_id=group.get('_id'), user_id=me.get('_id')).json().get('success'):
            return []

        members_ret = self.rocket.groups_members(room_id=group.get('_id')).json()
        if not members_ret.get('success'):
            return []

        return members_ret.get('members')

    def add_group(self, group_name):
        response = self.rocket.groups_create(group_name).json()
        return response.get("success") or response.get("errorType") == "error-duplicate-channel-name"

    def get_rc_user(self, user):
        username = user.get('username') if type(user) is dict else user
        if username in self.known_rc_users:
            return self.known_rc_users.get(username)

        if type(user) is dict:
            _api = self.rocket.users_info(user_id=user.get('_id'))
            if _api.status_code == 404:
                # Yes, some user are not gettable by id...
                _api = self.rocket.users_info(username=user.get('username'))
        else:
            _api = self.rocket.users_info(username=user)

        _api = _api.json()
        if not _api.get('success'):
            logger.error(f'Could not get user info for {username} because "{_api.get("error")}"')
            return
        rc_user = RCUser(_api.get('user'))
        self.known_rc_users[rc_user.username] = rc_user
        return rc_user

    def get_dn_of_rc_user_by_custom_field(self, rc_user):
        custom_field_value = rc_user.custom_fields.get(self.custom_user_field)
        base_dn = self.custom_user_field_conversions.get(custom_field_value, None)
        if base_dn is None:
            logger.error(f"User {rc_user.username} as custom_field_value {custom_field_value}, cannot convert!")
            return

        return f'uid={rc_user.username},{base_dn}'

    def get_user_avatar(self, user):
        avatar = self.rocket.users_get_avatar(user_id=user.get('_id'))
        if avatar.status_code == 404:
            avatar = self.rocket.users_get_avatar(username=user.get('username'))

        return avatar.content

    def get_all_users(self):
        all_users = []
        users_call = self.rocket.users_list().json()
        all_users.extend(users_call.get('users', []))

        while users_call.get('total') > users_call.get('count') + users_call.get('offset', 0):
            users_call = self.rocket.users_list(offset=int(users_call.get('count'))).json()
            all_users.extend(users_call.get('users', []))

        return all_users

    def add_userid_to_channel(self, user_id, rc_channel):
        rc_channel_id = self.get_channel_id(rc_channel)

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

