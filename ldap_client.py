import ldap3
import logging

logger = logging.getLogger(__name__)


class LDAPClient:
    def __init__(self, binddn="", password="", host="ldap://ldap:389", base_dn="", default_users_objectclasses=None,
                 default_groups_objectclasses=None, default_groups_basedn="", default_users_basedn="",
                 log_level=logging.INFO):
        logger.setLevel(log_level)

        self.ldap_base_dn = base_dn
        self.ldap_groups_basedn = self.default_ldap_groups_basedn = default_groups_basedn
        self.ldap_users_basedn = self.default_ldap_users_basedn = default_users_basedn
        self.ldap_users_objectclasses = self.default_users_objectclasses = \
            default_users_objectclasses if default_users_objectclasses is not None else []
        self.ldap_groups_objectclasses = self.default_groups_objectclasses = \
            default_groups_objectclasses if default_groups_objectclasses is not None else []

        self.ldap_server = ldap3.Server(host, get_info=ldap3.ALL)
        self.ldap_connection = ldap3.Connection(self.ldap_server, user=binddn, password=password)
        if not self.ldap_connection.bind():
            logger.error('Could not bind to LDAP! Invalid credentials? Wrong host?')

        self.all_users = self.get_all_users(base_dn)

    def update_settings(self, settings):
        self.ldap_groups_basedn = settings.get('groups_basedn', self.default_ldap_groups_basedn)
        if self.ldap_base_dn not in self.ldap_groups_basedn:
            self.ldap_groups_basedn = ",".join([self.ldap_groups_basedn, self.ldap_base_dn])

        self.ldap_users_basedn = settings.get('users_basedn', self.default_ldap_users_basedn)
        if self.ldap_base_dn not in self.ldap_users_basedn:
            self.ldap_users_basedn = ",".join([self.ldap_users_basedn, self.ldap_base_dn])

        self.ldap_users_objectclasses = settings.get('users_objectclasses', self.default_users_objectclasses)
        self.ldap_groups_objectclasses = settings.get('groups_objectclasses', self.default_groups_objectclasses)

        self.all_users = self.get_all_users(self.ldap_users_basedn)

    def get_group_member_dns(self, group_name):
        group_dn = f"{group_name},{self.ldap_groups_basedn}"

        self.ldap_connection.search(group_dn, '(objectClass=*)',
                                    attributes=['member', 'memberUid'])
        if not self.ldap_connection.response:
            return None

        attrs = self.ldap_connection.response[0].get('attributes', {})
        return [f"uid={i},{self.ldap_groups_basedn}" for i in attrs.get('memberUid')] + attrs.get('member', [])
        # return [i.split('=', 1)[1].split(',')[0] for i in attrs['member']] + attrs['memberUid']

    def add_group(self, group_name):
        group_dn = f"{group_name},{self.ldap_groups_basedn}"
        return self.ldap_connection.add(group_dn, object_class=self.ldap_groups_objectclasses,
                                        attributes={})

    def set_group_members(self, group_dn, member_dns):
        if self.ldap_groups_basedn not in group_dn:
            group_dn = ",".join([group_dn, self.ldap_groups_basedn])

        logger.debug(f'Replacing members of {group_dn} with members:\n{member_dns}')
        return self.ldap_connection.modify(group_dn, {'member': [(ldap3.MODIFY_REPLACE, member_dns)]})

    def get_user_by_rocketchat_id(self, rocketchat_id):
        self.ldap_connection.search(self.ldap_base_dn, f'(rocketchatId={rocketchat_id})')
        if not self.ldap_connection.response:
            logger.debug(f'No user found with ID {rocketchat_id}')
            return None

        return self.ldap_connection.response[0]

    def get_all_users(self, base_dn):
        self.ldap_connection.search(base_dn,
                                    f'(&{"".join([f"(objectClass={obc})" for obc in self.ldap_users_objectclasses])})',
                                    attributes=ldap3.ALL_ATTRIBUTES)
        return dict([(user.get('dn'), user) for user in self.ldap_connection.response])

    def add_or_update_user(self, dn, user_attributes, user_objectclasses=None):
        if not dn:
            return
        if user_objectclasses is None:
            user_objectclasses = self.ldap_users_objectclasses
        if self.ldap_base_dn not in dn:
            dn = ",".join([dn, self.ldap_users_basedn])

        if dn not in self.all_users.keys():
            # Create LDAP Entry
            if self.ldap_connection.add(dn, object_class=user_objectclasses, attributes=user_attributes):
                logger.info(f'    Created RC user "{user_attributes.get("uid")}" in LDAP')
                self.all_users[dn] = user_attributes
            else:
                logger.error(f'    Could not create RC user "{user_attributes.get("uid")}" in LDAP')
        else:
            # Update LDAP Entry. Replace only on changes
            changes = {}
            current_ldap_user_attributes = self.all_users[dn].get('attributes', {})
            for attribute_name, current_attribute_value in current_ldap_user_attributes.items():
                if attribute_name in user_attributes and user_attributes.get(attribute_name) != current_attribute_value:
                    changes[attribute_name] = [(ldap3.MODIFY_REPLACE, user_attributes.get(attribute_name))]

            if current_ldap_user_attributes.get('objectClass') != user_objectclasses:
                changes['objectClass'] = [(ldap3.MODIFY_REPLACE, user_objectclasses)]

            if changes:
                self.ldap_connection.modify(dn, changes, False)
                logger.info(f'    Updated RC user "{dn}" in LDAP')

    def delete_users_not_in_rc(self, all_ldap_users, all_rc_users):
        all_rc_users_uids = [user.get('username') for user in all_rc_users]

        for ldap_user in all_ldap_users.values():
            ldap_uid = ldap_user.get('attributes', {}).get('uid', [''])[0]
            if ldap_uid not in all_rc_users_uids:
                self.delete_dn(ldap_user.get("dn"))

    def delete_dn(self, dn):
        if self.ldap_connection.delete(dn):
            logger.info(f'Deleted from LDAP: {dn}')
            return True
        else:
            logger.error(f'Could not delete {dn}!')

    def add_rc_user_to_ldap_group(self, ldap_group, ldap_group_members, rc_username):
        rc_user_dn = f"uid={rc_username},{self.ldap_users_basedn}"
        if rc_user_dn not in self.all_users.keys():
            # Skip bots, ignored users and everyone not synced from RC beforehand
            return

        if rc_username not in ldap_group_members:
            ret = self.ldap_connection.modify(f"{ldap_group},{self.ldap_groups_basedn}",
                                              {'member': [(ldap3.MODIFY_ADD, [rc_user_dn])]})
            if ret:
                logger.info(f'    Added RC user {rc_username}')
            else:
                logger.error(f'      Could not add RC user {rc_username}!')

    def remove_users_from_ldap_group(self, ldap_group, rc_channel_members):
        rc_usernames = [rc_member.get('username') for rc_member in rc_channel_members]
        for ldap_member in rc_usernames:
            if ldap_member not in rc_usernames:
                ldap_user_dn = f"uid={ldap_member},{self.ldap_users_basedn}"
                ret = self.ldap_connection.modify(f"{ldap_group},{self.ldap_groups_basedn}",
                                                  {'member': [(ldap3.MODIFY_DELETE, [ldap_user_dn])]})
                if ret:
                    logger.info(f'    Removed LDAP user {ldap_member}')
                else:
                    logger.error(f'      Could not remove LDAP user {ldap_member}!')
