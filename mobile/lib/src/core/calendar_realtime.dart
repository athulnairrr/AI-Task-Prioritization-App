import 'package:supabase_flutter/supabase_flutter.dart';

import 'supabase_client.dart';

const _watchedTables = [
  'schedule_items',
  'google_calendar_event_mappings',
  'google_calendar_external_events',
  'google_calendar_connections',
];

/// Subscribes to Postgres changes on the tables Phase 7's Calendar sync
/// writes to -- mirrors web/src/lib/supabase/realtime.ts. No backend
/// "publish" call is needed: these tables are already in the
/// `supabase_realtime` publication (database/migrations/0004_calendar_sync.sql)
/// and already RLS-scoped to the caller's tenant, so a change only ever
/// reaches a client who could already read that row.
///
/// Deliberately coarse: `onChange` fires (debounced) for any insert/
/// update/delete on any of these tables and the caller just refetches
/// whatever it displays. Call `dispose()` on the returned subscription
/// (or store the channel and call `supabase.removeChannel`) when the
/// owning widget is disposed.
class CalendarRealtimeSubscription {
  CalendarRealtimeSubscription._(this._channel);

  final RealtimeChannel _channel;

  static CalendarRealtimeSubscription start(void Function() onChange) {
    final channel = supabase.channel('calendar-sync-changes');
    for (final table in _watchedTables) {
      channel.onPostgresChanges(
        event: PostgresChangeEvent.all,
        schema: 'public',
        table: table,
        callback: (_) => onChange(),
      );
    }
    channel.subscribe();
    return CalendarRealtimeSubscription._(channel);
  }

  Future<void> dispose() async {
    await supabase.removeChannel(_channel);
  }
}
