enum CalendarConnectionStatus {
  notConnected,
  connected,
  reauthRequired,
  error;

  static CalendarConnectionStatus fromApi(String value) {
    switch (value) {
      case 'connected':
        return CalendarConnectionStatus.connected;
      case 'reauth_required':
        return CalendarConnectionStatus.reauthRequired;
      case 'error':
        return CalendarConnectionStatus.error;
      default:
        return CalendarConnectionStatus.notConnected;
    }
  }
}

/// Mirrors the backend's CalendarConnectionOut schema
/// (backend/app/schemas/calendar.py). Never carries a token.
class CalendarConnection {
  const CalendarConnection({
    required this.status,
    this.googleAccountEmail,
    this.calendarId,
    this.connectedAt,
    this.lastError,
    this.calendarTimezone,
    this.hasWriteAccess = false,
    this.lastSyncedAt,
    this.watchActive = false,
  });

  final CalendarConnectionStatus status;
  final String? googleAccountEmail;
  final String? calendarId;
  final DateTime? connectedAt;
  final String? lastError;
  final String? calendarTimezone;
  final bool hasWriteAccess;
  // Phase 7: when the last incremental/full sync completed, and whether a
  // Google push-notification (watch) channel is currently registered and
  // unexpired. `watchActive == false` just means updates rely on the
  // manual sync fallback instead of push notifications.
  final DateTime? lastSyncedAt;
  final bool watchActive;

  factory CalendarConnection.fromJson(Map<String, dynamic> json) {
    return CalendarConnection(
      status: CalendarConnectionStatus.fromApi(json['status'] as String),
      googleAccountEmail: json['google_account_email'] as String?,
      calendarId: json['calendar_id'] as String?,
      connectedAt:
          json['connected_at'] == null ? null : DateTime.parse(json['connected_at'] as String),
      lastError: json['last_error'] as String?,
      calendarTimezone: json['calendar_timezone'] as String?,
      hasWriteAccess: json['has_write_access'] as bool? ?? false,
      lastSyncedAt:
          json['last_synced_at'] == null ? null : DateTime.parse(json['last_synced_at'] as String),
      watchActive: json['watch_active'] as bool? ?? false,
    );
  }
}

/// Mirrors the backend's CalendarSyncResultOut schema (POST /calendar/sync).
class CalendarSyncResult {
  const CalendarSyncResult({
    required this.synced,
    this.reason,
    this.fullResync = false,
    this.processed = 0,
    this.watchActive = false,
    this.lastSyncedAt,
  });

  final bool synced;
  final String? reason;
  final bool fullResync;
  final int processed;
  final bool watchActive;
  final DateTime? lastSyncedAt;

  factory CalendarSyncResult.fromJson(Map<String, dynamic> json) {
    return CalendarSyncResult(
      synced: json['synced'] as bool,
      reason: json['reason'] as String?,
      fullResync: json['full_resync'] as bool? ?? false,
      processed: json['processed'] as int? ?? 0,
      watchActive: json['watch_active'] as bool? ?? false,
      lastSyncedAt:
          json['last_synced_at'] == null ? null : DateTime.parse(json['last_synced_at'] as String),
    );
  }
}

/// Mirrors the backend's ExternalCalendarEventOut schema
/// (GET /calendar/external-events) -- a Calendar event this app did NOT
/// create, from the Phase 7 synced cache. Shown as a busy block; never
/// editable from this app.
class ExternalCalendarEvent {
  const ExternalCalendarEvent({
    required this.googleEventId,
    this.title,
    required this.start,
    required this.end,
    required this.allDay,
    required this.status,
  });

  final String googleEventId;
  final String? title;
  final DateTime start;
  final DateTime end;
  final bool allDay;
  final String status;

  factory ExternalCalendarEvent.fromJson(Map<String, dynamic> json) {
    return ExternalCalendarEvent(
      googleEventId: json['google_event_id'] as String,
      title: json['title'] as String?,
      start: DateTime.parse(json['start'] as String),
      end: DateTime.parse(json['end'] as String),
      allDay: json['all_day'] as bool? ?? false,
      status: json['status'] as String? ?? 'confirmed',
    );
  }
}

class BusyInterval {
  const BusyInterval({required this.start, required this.end});

  final DateTime start;
  final DateTime end;

  factory BusyInterval.fromJson(Map<String, dynamic> json) {
    return BusyInterval(
      start: DateTime.parse(json['start'] as String),
      end: DateTime.parse(json['end'] as String),
    );
  }
}

class Availability {
  const Availability({required this.rangeStart, required this.rangeEnd, required this.busy});

  final DateTime rangeStart;
  final DateTime rangeEnd;
  final List<BusyInterval> busy;

  factory Availability.fromJson(Map<String, dynamic> json) {
    return Availability(
      rangeStart: DateTime.parse(json['range_start'] as String),
      rangeEnd: DateTime.parse(json['range_end'] as String),
      busy: (json['busy'] as List<dynamic>)
          .map((e) => BusyInterval.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}
