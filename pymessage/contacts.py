"""Local address labels from the public macOS Contacts scripting interface."""
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import subprocess

from .util import atomic_write, digest, dumps

# Bulk property requests avoid one Apple Event per contact. IDs guard against
# records being added/reordered between requests. Contact IDs link multiple
# phone/email addresses without guessing from names. Notes, birthdays, postal
# addresses, and pictures are never requested.
CONTACTS_SCRIPT = r'''
function run() {
    var people = Application('/System/Applications/Contacts.app').people;
    var ids = people.id();
    var names = people.name();
    var phones = people.phones.value();
    var emails = people.emails.value();
    if (JSON.stringify(ids) !== JSON.stringify(people.id()) ||
        names.length !== ids.length || phones.length !== ids.length || emails.length !== ids.length) {
        throw new Error('Contacts changed during reading. Please try again.');
    }
    return JSON.stringify(ids.map(function(id, i) {
        if (!Array.isArray(phones[i]) || !Array.isArray(emails[i])) {
            throw new Error('Contacts returned an unexpected address list.');
        }
        return {id: id, name: names[i], phones: phones[i], emails: emails[i]};
    }));
}
'''


def address_key(address):
    if not isinstance(address,str):
        return None
    address = address.strip().casefold()
    if address.startswith('mailto:'):
        address = address[7:]
    if '@' in address:
        return 'email:'+address
    if address.startswith('tel:'):
        address = address[4:]
    if not re.fullmatch(r'\+?[0-9()\s.\-]+',address):
        return None
    digits = re.sub(r'[^0-9]','',address)
    if digits.startswith('00'):
        digits = digits[2:]
    # Explicit North American +1/ten-digit equivalence; never guess arbitrary
    # international suffixes or combine extensions with the base number.
    if len(digits)==11 and digits.startswith('1'):
        digits = digits[1:]
    if not 7 <= len(digits) <= 15:
        return None
    return 'phone:'+digits


def read_macos_contacts():
    if platform.system() != 'Darwin':
        raise RuntimeError('Matching Apple Contacts is available on macOS.')
    try:
        result = subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',CONTACTS_SCRIPT],
                                capture_output=True,text=True,encoding='utf-8',timeout=90)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('Contacts did not respond in time. Check for a macOS permission prompt, then try Match contacts again.') from error
    if result.returncode:
        if '-1743' in result.stderr or 'not authorized' in result.stderr.lower():
            raise RuntimeError('Allow the app launching Py-Message to access Contacts in System Settings > '
                               'Privacy & Security > Automation, then try Match contacts again. '
                               'This permission is separate from Full Disk Access.')
        # Do not echo arbitrary Contacts error output containing personal data.
        raise RuntimeError('Could not read Contacts. Open the Contacts app, check for a permission prompt, '
                           'and try Match contacts again. Your previously saved labels were kept.')
    try:
        records = json.loads(result.stdout)
        if not isinstance(records,list) or any(not isinstance(row,dict) or
            not isinstance(row.get('phones'),list) or not isinstance(row.get('emails'),list) for row in records):
            raise ValueError('Unexpected contact format')
        return records
    except (ValueError,TypeError) as error:
        raise RuntimeError('Contacts returned an unexpected format. Your previously saved labels were kept.') from error


class ContactBook:
    def __init__(self, labels=None, updated=None, count=0, identities=None):
        self.labels = labels or {}
        self.updated = updated
        self.count = count
        self.identity_ready = identities is not None
        self.identities = identities or {}

    @classmethod
    def from_records(cls, records):
        candidates = {}
        owners = {}
        count = 0
        for record in records:
            name = record.get('name')
            if not isinstance(name,str) or not name.strip():
                continue
            name = name.strip()
            count += 1
            keys = {address_key(value) for value in [*record.get('phones',[]),*record.get('emails',[])]}
            keys.discard(None)
            identity = 'contact:'+str(record.get('id') or digest(sorted(keys)))
            for address in [*record.get('phones',[]),*record.get('emails',[])]:
                key = address_key(address)
                if key:
                    candidates.setdefault(key,set()).add(name)
                    owners.setdefault(key,set()).add(identity)
        # Two different names sharing a number/email stay unresolved.
        labels = {key:next(iter(names)) for key,names in candidates.items() if len(names)==1}
        identities = {key:next(iter(ids)) for key,ids in owners.items() if len(ids)==1}
        return cls(labels,datetime.now(timezone.utc).isoformat(),count,identities)

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text())
            if data.get('version') not in (1,2) or not isinstance(data['labels'],dict):
                return cls()
            if not all(isinstance(k,str) and isinstance(v,str) for k,v in data['labels'].items()):
                return cls()
            if not isinstance(data.get('updated'),str) or not isinstance(data.get('count'),int):
                return cls()
            identities = data.get('identities') if data['version']==2 else None
            if data['version']==2 and (not isinstance(identities,dict) or
                not all(isinstance(k,str) and isinstance(v,str) for k,v in identities.items())):
                return cls()
            return cls(data['labels'],data['updated'],data['count'],identities)
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            return cls()

    def save(self, path):
        # atomic_write creates its replacement with private (0600) permissions.
        atomic_write(path,dumps({'version':2 if self.identity_ready else 1,'labels':self.labels,'updated':self.updated,
                               'count':self.count,'identities':self.identities})+'\n')

    def name(self, address):
        return self.labels.get(address_key(address),address)

    def matches(self, address, search):
        return bool(search and search.casefold() in (self.name(address) or '').casefold())

    def chat_name(self, original, metadata, guid, participants):
        raw = json.loads(metadata or '{}')
        if raw.get('display_name') or raw.get('html_file'):
            return original
        labels = [self.name(address) or 'Unknown' for address in participants]
        if labels and labels != participants:
            # Only replace address/generated titles. Keep intentional labels.
            generated = original==guid or original in participants or address_key(original) is not None or (original or '').startswith('chat')
            if generated:
                return ', '.join(labels)
        return original
