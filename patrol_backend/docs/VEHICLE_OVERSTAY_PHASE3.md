# Vehicle Overstay SOS — Phase 3

Per-site recipient roles + delivery via **visitor `NotificationLog`** (same inbox as approve/cancel).

> Note: An earlier draft used `TrackingAlert`; that was reverted. Livetracking migration
> `0005_revert_vehicle_overstay_alert` restores TrackingAlert to pre-overstay shape.

## APIs

### GET `/visitors/v5/overstay-alert-recipients/?site_id=`

```json
{
  "site_id": "...",
  "site_name": "Gate A",
  "role_ids": ["1", "2"],
  "results": [
    {
      "id": "...",
      "site_id": "...",
      "site_name": "Gate A",
      "recipient_role_id": 1,
      "recipient_role_name": "so"
    }
  ]
}
```

### PUT `/visitors/v5/overstay-alert-recipients/`

```json
{ "site_id": "...", "role_ids": [1, 2] }
```

Empty `role_ids` clears recipients (worker defers until roles are set).

## Notification payload

`NotificationLog.type = "vehicle_overstay"`

- Always: org **Admin** users for the site's location (not shown in the UI role picker)
- One log row **per recipient user**
- `related_entry` → the overstaying `VisitorEntry`
- FCM via existing `notify_user` (android/ios tokens)
- Inbox: **Notifications** (visitor notification history), not Tracking Alerts

Example `data`:

```json
{
  "type": "vehicle_overstay",
  "entry_id": "...",
  "visitor_name": "...",
  "vehicle_number": "ABC1234",
  "site_id": "...",
  "site_name": "Gate A",
  "check_in_time": "...",
  "status": "checked_in",
  "location_id": "..."
}
```

## UI

- Visitor tab **Overstay Alert Roles** → `/visitor/overstay-alert-roles`
- Alerts appear under the normal Notifications page

## Ops

```bash
python manage.py migrate livetracking   # applies 0005 revert if 0004 was applied
# restart celery worker + beat
```

Recipient model (unchanged): `visitor.0014_site_vehicle_overstay_recipient`

## Worker behaviour

1. checked-in + vehicle_number + past `vehicle_overstay_hours`
2. skip org whitelist
3. once per visit (`overstay_alert_sent_at`)
4. notify configured roles **plus org Admin**; if neither exists, leave sent_at null
