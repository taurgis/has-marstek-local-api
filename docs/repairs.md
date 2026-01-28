# Repairs

Home Assistant may surface **Repairs** when the integration detects fixable issues.

## Cannot connect

If Home Assistant can’t connect to the device (for example, after a DHCP IP change), the integration can raise a repair flow that lets you:

- Enter an updated **Host** and **Port**
- Validate device identity (the device unique ID must match)
- Apply the fix and automatically reload the config entry

If the unique ID doesn’t match, the repair refuses the update to prevent accidentally pointing the entry at a different device.

## Other cases

Repairs may also be used for other fixable issues over time, but the current primary flow is the connection repair above.

<img src="screenshots/repair-list.png" alt="Repair list" width="340" />
<img src="screenshots/repair-detail.png" alt="Repair detail" width="340" />
<img src="screenshots/repair-fix.png" alt="Repair fix" width="340" />
