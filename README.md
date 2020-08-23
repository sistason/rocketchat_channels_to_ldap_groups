# rocketchat_ldap_sync

Syncs, very basicly, from Rocket.Chat to LDAP and back, 
complementing the already existing LDAP-sync.

- Sync Rocket.Chat channels to LDAP-groups 
- Sync Rocket.Chat users to LDAP
- Sync LDAP-group memberships to Rocket.Chat channel members
- (LDAP->RC Sync is already builtin)

## Configuration

fill login-data, config-basics and channels-to-sync into your copy of 
`config_default.yaml`, or use the options as environment-variables and
supply the channels via --channel. Examples:

```
python3 rc_sync.py --config=config.yaml sync_users_rc_to_ldap

OR

RC_USERNAME=admin RC_PASSWORD=foo [etc...] python3 rc_sync.py \
    --channel admins=cn=admins \
    --channel channel1=cn=ldap_group \
    sync_users_rc_to_ldap sync_channels_rc_to_ldap 
```

You can use --repeat_every_seconds=$SECONDS to run periodically.

### RC_CUSTOM_USER_FIELD

Rocket.Chat can be configured to use custom user fields. You can use
that to select 1 field from which you generate user base_dns.
For example, you have a custom field "company_branch" and would use
the RC_CUSTOM_USER_FIELD_CONVERSIONS to set for example the "IT"
department to use ou=tech_users and the "HR" department to use ou=users

### Groups: Rocket.Chat -> LDAP

Get the channel-members of the SYNC-channels and sync to LDAP groups 
with dn and members=LDAP-DNs of the Rocket.Chat member-uids.

### Groups: LDAP -> Rocket.Chat

Sync the members of the LDAP-groups to the SYNC-channels. Rocket.Chat
already has this functionality built-in, but only for 1 base_dn. Use
this if your LDAP is more complicated with many subtrees to be handled
differently

### Users: Rocket.Chat -> LDAP

Syncs all current Rocket.Chat-users to LDAP, with cn, mail, uid, 
thumbnailPhoto and pw, and deletes those in LDAP, but not in 
Rocket.Chat anymore. 
(only searches/deletes for the given basedn and the objectclasses.
Your other important LDAP-accounts should not match that.)

This is meant to have Rocket.Chat as central user directory/management 
and use LDAP for the services not able to use Oauth2 to 
authenticate to Rocket.Chat.


## LDAP Bcrypt

This requires bcrypt to be patched into your LDAP server, as 
Rocket.Chat uses sha256-bcrypt-hashing for the passwords, which is not 
supported by LDAP upstream. Get the module via
[this repo](https://github.com/sistason/openldap-sha256-bcrypt) and
add to your container as
[described here](https://github.com/osixia/docker-openldap/issues/150).
 
Load the module and set the `olcPasswordHash` to be `{BCRYPT}` in 
the **olcDatabase={-1}frontend,cn=config**, as cn=config paramenters are 
evaluated before dynamic module loading! 

```
# Add bcrypt support
dn: cn=module{0},cn=config
changetype: modify
add: olcModuleLoad
olcModuleLoad: pw-bcrypt.so

# set bcrypt as default pw hash as Rocketchat uses
dn: olcDatabase={-1}frontend,cn=config
changetype: modify
add: olcPasswordHash
olcPasswordHash: {BCRYPT}
```

See 
[this](https://www.openldap.org/lists/openldap-software/200708/msg00084.html) 
and [this](http://www.openldap.org/its/index.cgi?findid=5082), where the
workaround is described.