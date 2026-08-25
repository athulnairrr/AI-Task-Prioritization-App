import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/calendar_realtime.dart';
import '../../design/format.dart';
import '../../design/tokens.dart';
import '../../design/widgets/empty_state.dart';
import '../../scheduling/data/schedule_api_client.dart';
import '../../scheduling/data/schedule_models.dart';
import '../data/calendar_api_client.dart';
import '../data/calendar_models.dart';

enum _LoadStatus { loading, loaded, error }

/// The Calendar tab: a single day's agenda (Google events + AI-created
/// work blocks + external busy periods, merged chronologically) plus
/// sync status -- deliberately not a full month/week grid (see the
/// brief: "do not attempt to recreate the entire Google Calendar app").
class CalendarScreen extends StatefulWidget {
  const CalendarScreen({
    super.key,
    CalendarApiClient? calendarApiClient,
    ScheduleApiClient? scheduleApiClient,
    this.enableRealtime = true,
  })  : _injectedCalendarApi = calendarApiClient,
        _injectedScheduleApi = scheduleApiClient;

  // Injectable for tests -- default to real clients otherwise.
  final CalendarApiClient? _injectedCalendarApi;
  final ScheduleApiClient? _injectedScheduleApi;
  // Off in widget tests -- a real realtime subscription needs an
  // initialized Supabase client and opens a real websocket, neither of
  // which a hermetic widget test should depend on.
  final bool enableRealtime;

  @override
  State<CalendarScreen> createState() => _CalendarScreenState();
}

class _CalendarScreenState extends State<CalendarScreen> {
  late final _calendarApi = widget._injectedCalendarApi ?? CalendarApiClient();
  late final _scheduleApi = widget._injectedScheduleApi ?? ScheduleApiClient();

  DateTime _day = DateTime.now();
  _LoadStatus _status = _LoadStatus.loading;
  String? _error;
  CalendarConnection? _connection;
  List<ScheduleItem> _scheduleItems = const [];
  List<ExternalCalendarEvent> _externalEvents = const [];
  List<NeedsAttentionItem> _needsAttention = const [];
  bool _syncing = false;
  CalendarRealtimeSubscription? _realtime;

  @override
  void initState() {
    super.initState();
    _load();
    if (widget.enableRealtime) {
      _realtime = CalendarRealtimeSubscription.start(_load);
    }
  }

  @override
  void dispose() {
    _realtime?.dispose();
    super.dispose();
  }

  ({DateTime start, DateTime end}) get _range {
    final start = DateTime(_day.year, _day.month, _day.day);
    return (start: start, end: start.add(const Duration(days: 1)));
  }

  Future<void> _load() async {
    if (!mounted) return;
    setState(() => _status = _LoadStatus.loading);
    final range = _range;
    try {
      final results = await Future.wait([
        _calendarApi.getConnection(),
        _scheduleApi.listScheduleItems(range.start, range.end),
        // 404s with "Calendar not connected" for a user who hasn't
        // connected yet -- expected, not an error; the connection card
        // below already communicates that state, so this screen should
        // still render it instead of failing outright.
        _calendarApi
            .getExternalEvents(range.start, range.end)
            .catchError((_) => <ExternalCalendarEvent>[]),
        _scheduleApi.listNeedsAttention(),
      ]);
      if (!mounted) return;
      setState(() {
        _connection = results[0] as CalendarConnection;
        _scheduleItems = (results[1] as List<ScheduleItem>)..sort((a, b) => a.startsAt.compareTo(b.startsAt));
        _externalEvents = (results[2] as List<ExternalCalendarEvent>)
          ..sort((a, b) => a.start.compareTo(b.start));
        _needsAttention = results[3] as List<NeedsAttentionItem>;
        _status = _LoadStatus.loaded;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not load your calendar. Pull down to retry.';
        _status = _LoadStatus.error;
      });
    }
  }

  Future<void> _syncNow() async {
    setState(() => _syncing = true);
    try {
      await _calendarApi.syncCalendar();
      await _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Sync failed. Try again.')));
      }
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  Future<void> _connect() async {
    try {
      final url = await _calendarApi.getConnectUrl();
      await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
    } catch (_) {}
  }

  void _changeDay(int delta) {
    setState(() => _day = _day.add(Duration(days: delta)));
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Calendar')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(Spacing.lg),
          children: [
            _ConnectionCard(
              connection: _connection,
              syncing: _syncing,
              onSyncNow: _syncNow,
              onConnect: _connect,
            ),
            const SizedBox(height: Spacing.lg),
            if (_needsAttention.isNotEmpty) ...[
              _NeedsAttentionBanner(items: _needsAttention),
              const SizedBox(height: Spacing.lg),
            ],
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                IconButton(icon: const Icon(Icons.chevron_left), onPressed: () => _changeDay(-1)),
                Text(Format.dayHeading(_day), style: Theme.of(context).textTheme.titleMedium),
                IconButton(icon: const Icon(Icons.chevron_right), onPressed: () => _changeDay(1)),
              ],
            ),
            const SizedBox(height: Spacing.sm),
            if (_status == _LoadStatus.loading)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: Spacing.xxl),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_status == _LoadStatus.error)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: Spacing.xl),
                child: Center(child: Text(_error ?? 'Something went wrong.')),
              )
            else
              ..._buildAgenda(context),
          ],
        ),
      ),
    );
  }

  List<Widget> _buildAgenda(BuildContext context) {
    final entries = <(DateTime, DateTime, Widget)>[
      for (final item in _scheduleItems)
        (item.startsAt, item.endsAt, _AgendaTile(
          time: Format.timeRange(item.startsAt, item.endsAt),
          title: item.title,
          icon: item.needsAttention ? Icons.warning_amber_rounded : Icons.auto_awesome,
          iconColor: item.needsAttention ? const Color(0xFFB45309) : null,
          subtitle: item.needsAttention ? 'Needs attention' : 'AI-scheduled work block',
        )),
      for (final event in _externalEvents)
        (event.start, event.end, _AgendaTile(
          time: event.allDay ? 'All day' : Format.timeRange(event.start, event.end),
          title: event.title ?? '(No title)',
          icon: Icons.event_outlined,
          subtitle: 'Calendar event',
        )),
    ]..sort((a, b) => a.$1.compareTo(b.$1));

    if (entries.isEmpty) {
      return const [
        EmptyState(icon: Icons.calendar_today_outlined, title: 'Nothing scheduled', message: 'This day is open.'),
      ];
    }
    return [for (final e in entries) Padding(padding: const EdgeInsets.only(bottom: Spacing.sm), child: e.$3)];
  }
}

class _ConnectionCard extends StatelessWidget {
  const _ConnectionCard({
    required this.connection,
    required this.syncing,
    required this.onSyncNow,
    required this.onConnect,
  });

  final CalendarConnection? connection;
  final bool syncing;
  final VoidCallback onSyncNow;
  final VoidCallback onConnect;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final connected = connection?.status == CalendarConnectionStatus.connected;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(Spacing.md),
        child: Row(
          children: [
            Icon(Icons.calendar_month, color: connected ? scheme.primary : scheme.outline),
            const SizedBox(width: Spacing.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    connected ? 'Google Calendar connected' : 'Google Calendar not connected',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  if (connected)
                    Text(
                      '${connection!.watchActive ? "Live sync" : "Manual sync"} · Last synced: '
                      '${connection!.lastSyncedAt?.toLocal() ?? "never"}',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                    ),
                ],
              ),
            ),
            if (connected)
              IconButton(
                icon: syncing
                    ? const SizedBox(height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.sync),
                tooltip: 'Sync now',
                onPressed: syncing ? null : onSyncNow,
              )
            else
              TextButton(onPressed: onConnect, child: const Text('Connect')),
          ],
        ),
      ),
    );
  }
}

class _NeedsAttentionBanner extends StatelessWidget {
  const _NeedsAttentionBanner({required this.items});

  final List<NeedsAttentionItem> items;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(Spacing.md),
      decoration: BoxDecoration(
        color: const Color(0xFFFEF3C7),
        border: Border.all(color: const Color(0xFFF59E0B)),
        borderRadius: BorderRadius.circular(Corners.md),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            items.length == 1 ? '1 item needs attention' : '${items.length} items need attention',
            style: const TextStyle(fontWeight: FontWeight.w700, color: Color(0xFFB45309)),
          ),
          const SizedBox(height: 4),
          for (final item in items)
            Text('• ${item.title}', style: const TextStyle(fontSize: 13, color: Color(0xFF92400E))),
        ],
      ),
    );
  }
}

class _AgendaTile extends StatelessWidget {
  const _AgendaTile({
    required this.time,
    required this.title,
    required this.icon,
    required this.subtitle,
    this.iconColor,
  });

  final String time;
  final String title;
  final IconData icon;
  final String subtitle;
  final Color? iconColor;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(Spacing.md),
        child: Row(
          children: [
            Icon(icon, size: 20, color: iconColor ?? scheme.onSurfaceVariant),
            const SizedBox(width: Spacing.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(time, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13)),
                  Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
                  Text(subtitle, style: Theme.of(context).textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
