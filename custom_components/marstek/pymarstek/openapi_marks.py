"""Per-IP Open API quirks the UDP client has to honour on the wire.

Two marks live here:

* **reset-prone** — Control firmware below 150 reboots its Open API stack when
  requests overlap, so those IPs exchange one request at a time.
* **retransmit-safe** — firmware known to tolerate a duplicate read-only
  unicast, which is how the client rides out Wi-Fi power-save drops.

Reset-prone marks are reference counted by *owner* (a config entry id) so a
SETUP_RETRY IP change cannot leave a stale mark that later serializes an
unrelated 150+ device which reused the address.
"""

from __future__ import annotations

ANONYMOUS_OWNER = "*"


class OpenApiMarks:
    """Registry of per-device Open API quirk marks."""

    def __init__(self) -> None:
        self._reset_prone: set[str] = set()
        self._reset_prone_owners: dict[str, set[str]] = {}
        self._retransmit_safe: set[str] = set()

    def set_reset_prone(
        self, device_ip: str, prone: bool, *, owner: str | None = None
    ) -> None:
        """Add or drop one owner's reset-prone mark for a device IP."""
        owner_key = owner or ANONYMOUS_OWNER
        owners = self._reset_prone_owners.setdefault(device_ip, set())
        if prone:
            owners.add(owner_key)
            self._reset_prone.add(device_ip)
            self._retransmit_safe.discard(device_ip)
            return
        owners.discard(owner_key)
        if not owners:
            self._reset_prone_owners.pop(device_ip, None)
            self._reset_prone.discard(device_ip)

    def clear_reset_prone(self, device_ip: str, *, owner: str | None = None) -> None:
        """Stop serializing Open API traffic for a device IP."""
        if owner is None:
            self._reset_prone_owners.pop(device_ip, None)
            self._reset_prone.discard(device_ip)
            return
        self.set_reset_prone(device_ip, False, owner=owner)

    def clear_owner(self, owner: str) -> None:
        """Drop every reset-prone mark held by one config entry."""
        for device_ip in list(self._reset_prone_owners):
            self.set_reset_prone(device_ip, False, owner=owner)

    def is_reset_prone(self, device_ip: str, *, owner: str | None = None) -> bool:
        """Return True when *device_ip* is marked reset-prone for *owner*."""
        if device_ip not in self._reset_prone:
            return False
        if owner is None:
            return True
        owners = self._reset_prone_owners.get(device_ip, set())
        return owner in owners or ANONYMOUS_OWNER in owners or not owners

    def transfer_reset_prone(self, old_ip: str, new_ip: str, *, owner: str) -> None:
        """Move one owner's reset-prone mark when a device changes IP."""
        if old_ip == new_ip:
            return
        owners = self._reset_prone_owners.get(old_ip, set())
        marked = (
            owner in owners
            or ANONYMOUS_OWNER in owners
            or (old_ip in self._reset_prone and not owners)
        )
        if not marked:
            return
        self.set_reset_prone(old_ip, False, owner=owner)
        if ANONYMOUS_OWNER in self._reset_prone_owners.get(old_ip, set()):
            self.set_reset_prone(old_ip, False, owner=ANONYMOUS_OWNER)
        elif old_ip in self._reset_prone and old_ip not in self._reset_prone_owners:
            self.clear_reset_prone(old_ip)
        self.set_reset_prone(new_ip, True, owner=owner)

    def set_retransmit_safe(self, device_ip: str, enabled: bool) -> None:
        """Allow or deny Wi-Fi silent-wait retransmission for a device IP.

        Opt in only after ``FirmwareProfile.openapi_wifi_retransmit_safe``.
        A reset-prone mark always wins and drops this flag.
        """
        if enabled and device_ip not in self._reset_prone:
            self._retransmit_safe.add(device_ip)
            return
        self._retransmit_safe.discard(device_ip)

    def is_retransmit_safe(self, device_ip: str) -> bool:
        """Return True when *device_ip* may receive extra read-only unicasts."""
        return device_ip in self._retransmit_safe and device_ip not in self._reset_prone

    def clear(self) -> None:
        """Forget every mark."""
        self._reset_prone.clear()
        self._reset_prone_owners.clear()
        self._retransmit_safe.clear()
