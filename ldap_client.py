import ldap3
import logging

logger = logging.getLogger(__name__)


class LDAPClient:
    def __init__(self, ldap_binddn="", ldap_password="", ldap_host="ldap://ldap:389", ldap_group_basedn='',
                 ldap_users_basedn='', ldap_group_objectclasses=None, ldap_users_objectclasses=None,
                 log_level=logging.INFO):
        self.ldap_binddn = ldap_binddn
        self.ldap_password = ldap_password
        self.ldap_group_basedn = ldap_group_basedn
        self.ldap_users_basedn = ldap_users_basedn
        self.ldap_users_objectclasses = ldap_users_objectclasses
        self.ldap_group_objectclasses = ldap_group_objectclasses
        logger.setLevel(log_level)

        self.ldap_server = ldap3.Server(ldap_host, get_info=ldap3.ALL)
        self.ldap_connection = ldap3.Connection(self.ldap_server, user=self.ldap_binddn, password=self.ldap_password)
        if not self.ldap_connection.bind():
            logger.error('Could not bind to LDAP! Invalid credentials? Wrong host?')

        self.all_users = {}

    def get_group_member_uids(self, group_dn='', group_name=''):
        if group_name:
            group_dn = f"{group_name},{self.ldap_group_basedn}"
        elif group_dn:
            group_name = group_dn.split('=', 1)[1].split(',')[0]
        else:
            logger.error('Either group_dn or group_name required')
            return None

        self.ldap_connection.search(group_dn, '(objectClass=*)',
                                    attributes=['member', 'memberUid'])
        if not self.ldap_connection.response:
            self.ldap_connection.add(group_dn, object_class=self.ldap_group_objectclasses,
                                     attributes={'cn': group_name})
            return []

        attrs = self.ldap_connection.response[0].get('attributes', {})
        return [i.split('=', 1)[1].split(',')[0] for i in attrs['member']] + attrs['memberUid']

    def get_all_users(self):
        self.ldap_connection.search(self.ldap_users_basedn,
                                    f'(&{"".join([f"(objectClass={obc})" for obc in self.ldap_users_objectclasses])})',
                                    attributes=ldap3.ALL_ATTRIBUTES)
        return dict([(user.get('dn'), user) for user in self.ldap_connection.response])

    def add_or_update_user(self, all_ldap_users, dn, ldap_attributes):
        if dn not in all_ldap_users.keys():
            # Create LDAP Entry
            self.ldap_connection.add(dn, object_class=self.ldap_users_objectclasses,
                                     attributes=ldap_attributes)
            logger.info(f'    Created RC user "{ldap_attributes.get("uid")}" in LDAP')
        else:
            # Update LDAP Entry
            self.ldap_connection.modify(dn,
                                        {'cn': [(ldap3.MODIFY_REPLACE, ldap_attributes.get('cn'))],
                                         'mail': [(ldap3.MODIFY_REPLACE, ldap_attributes.get('mail'))],
                                         'uid': [(ldap3.MODIFY_REPLACE, ldap_attributes.get('uid'))],
                                         'userPassword': [(ldap3.MODIFY_REPLACE, ldap_attributes.get('userPassword'))],
                                         'thumbnailPhoto': [(ldap3.MODIFY_REPLACE, ldap_attributes.get('thumbnailPhoto'))],
                                         'jpegPhoto': [(ldap3.MODIFY_REPLACE, ldap_attributes.get('jpegPhoto'))]},
                                        False)
            logger.info(f'    Updated RC user "{ldap_attributes.get("uid")}" in LDAP')

    def delete_users_not_in_rc(self, all_ldap_users, all_rc_users):
        all_rc_users_uids = [user.get('username') for user in all_rc_users]

        for ldap_user in all_ldap_users.values():
            ldap_uid = ldap_user.get('attributes', {}).get('uid', [''])[0]
            if ldap_uid not in all_rc_users_uids:

                if not self.ldap_connection.delete(ldap_user.get('dn')):
                    logger.info(f'Deleted from LDAP: {ldap_uid}')
                else:
                    logger.error(f'Could not delete {ldap_user.get("dn")}!')

    def add_rc_user_to_ldap_group(self, ldap_group, ldap_group_members, rc_username):
        rc_user_dn = f"uid={rc_username},{self.ldap_users_basedn}"
        if rc_user_dn not in self.all_users.keys():
            # Skip bots, ignored users and everyone not synced from RC beforehand
            return

        if rc_username not in ldap_group_members:
            ret = self.ldap_connection.modify(f"{ldap_group},{self.ldap_group_basedn}",
                                              {'member': [(ldap3.MODIFY_ADD, [rc_user_dn])]})
            if ret:
                logger.info(f'    Added RC user {rc_username}')
            else:
                logger.error(f'      Could not add RC user {rc_username}!')

    def delete_ldap_users_not_in_rc_channel(self, ldap_group, rc_channel_members):
        rc_usernames = [rc_member.get('username') for rc_member in rc_channel_members]
        for ldap_member in rc_usernames:
            if ldap_member not in rc_usernames:
                ldap_user_dn = f"uid={ldap_member},{self.ldap_users_basedn}"
                ret = self.ldap_connection.modify(f"{ldap_group},{self.ldap_group_basedn}",
                                                  {'member': [(ldap3.MODIFY_DELETE, [ldap_user_dn])]})
                if ret:
                    logger.info(f'    Removed LDAP user {ldap_member}')
                else:
                    logger.error(f'      Could not remove LDAP user {ldap_member}!')
