import 'package:flutter/material.dart';

import '../../calendar/data/calendar_api_client.dart';
import '../../calendar/data/calendar_models.dart';
import '../../core/calendar_realtime.dart';
import '../../design/format.dart';
import '../../design/tokens.dart';
import '../../design/widgets/empty_state.dart';
import '../../design/widgets/error_state.dart';
import '../../design/widgets/loading_skeleton.dart';
import '../../design/widgets/priority_badge.dart';
import '../../design/widgets/section_label.dart';
import '../../scheduling/data/schedule_api_client.dart';
import '../../scheduling/data/schedule_models.dart';
import '../../tasks/data/task_api_client.dart';
import '../../tasks/data/task_model.dart';
import '../../tasks/presentation/task_detail_screen.dart';

enum _LoadStatus { loading, loaded, error }

/// The home screen: "what does my day look like right now". Combines
/// prioritized tasks, applied schedule items, external Calendar busy
/// blocks, and any items needing attention into one glanceable view --
/// deliberately not a generic task list (that's the Tasks tab).
class TodayScreen extends StatefulWidget {
  const TodayScreen({
    super.key,
    this.enableRealtime = true,
    TaskApiClient? taskApiClient,
    ScheduleApiClient? scheduleApiClient,
    CalendarApiClient? calendarApiClient,
  })  : _injectedTaskApi = taskApiClient,
        _injectedScheduleApi = scheduleApiClient,
        _injectedCalendarApi = calendarApiClient;

  // Off in widget tests -- see CalendarScreen's `enableRealtime` doc.
  final bool enableRealtime;
  // Injectable for tests -- default to real clients otherwise.
  final TaskApiClient? _injectedTaskApi;
  final ScheduleApiClient? _injectedScheduleApi;
  final CalendarApiClient? _injectedCalendarApi;

  @override
  State<TodayScreen> createState() => _TodayScreenState();
}

class _TodayScreenState extends State<TodayScreen> {
  late final _taskApi = widget._injectedTaskApi ?? TaskApiClient();
  late final _scheduleApi = widget._injectedScheduleApi ?? ScheduleApiClient();
  late final _calendarApi = widget._injectedCalendarApi ?? CalendarApiClient();

  _LoadStatus _status = _LoadStatus.loading;
  String? _error;
  List<PrioritizedTask> _highPriority = const [];
  List<ScheduleItem> _scheduleItems = const [];
  List<ExternalCalendarEvent> _externalEvents = const [];
  List<NeedsAttentionItem> _needsAttention = const [];
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

  ({DateTime start, DateTime end}) get _today {
    final now = DateTime.now();
    final start = DateTime(now.year, now.month, now.day);
    return (start: start, end: start.add(const Duration(days: 1)));
  }

  Future<void> _load() async {
    if (!mounted) return;
    setState(() => _status = _LoadStatus.loading);
    final range = _today;
    // One silent retry before surfacing an error: right after sign-in, the
    // very first authenticated request(s) this screen fires can race a
    // just-issued Supabase session (token not yet reflected everywhere it
    // needs to be, or a first-connection network hiccup) and come back
    // 401/failed even though the session is genuinely valid -- confirmed
    // live on a physical device immediately post-sign-in, and confirmed to
    // recover instantly on a manual retry. A brief automatic retry absorbs
    // that one-off transient so the user isn't shown a scary error for
    // something that fixes itself a moment later.
    for (var attempt = 0; attempt < 2; attempt++) {
      try {
        final results = await Future.wait([
          _taskApi.listPrioritizedTasks(status: TaskStatus.pending),
          _scheduleApi.listScheduleItems(range.start, range.end),
          // 404s with "Calendar not connected" for a user who hasn't
          // connected Google Calendar yet -- that's an expected, common
          // state (not an error), so this screen still renders the rest of
          // today's plan rather than failing outright over it.
          _calendarApi
              .getExternalEvents(range.start, range.end)
              .catchError((_) => <ExternalCalendarEvent>[]),
          _scheduleApi.listNeedsAttention(),
        ]);
        if (!mounted) return;
        final prioritized = (results[0] as List<PrioritizedTask>)
            .where((t) => t.isPrioritized)
            .toList()
          ..sort((a, b) => b.priorityScore!.compareTo(a.priorityScore!));
        setState(() {
          _highPriority = prioritized.take(4).toList();
          _scheduleItems = (results[1] as List<ScheduleItem>)..sort((a, b) => a.startsAt.compareTo(b.startsAt));
          _externalEvents = (results[2] as List<ExternalCalendarEvent>)
            ..sort((a, b) => a.start.compareTo(b.start));
          _needsAttention = results[3] as List<NeedsAttentionItem>;
          _status = _LoadStatus.loaded;
        });
        return;
      } catch (e) {
        if (!mounted) return;
        if (attempt == 0) {
          await Future.delayed(const Duration(milliseconds: 500));
          continue;
        }
        setState(() {
          _error = 'Could not load today\'s plan. Pull down to retry.';
          _status = _LoadStatus.error;
        });
      }
    }
  }

  Duration get _focusTimeAvailable {
    const workStartHour = 9;
    const workEndHour = 18;
    final range = _today;
    final workStart = DateTime(range.start.year, range.start.month, range.start.day, workStartHour);
    final workEnd = DateTime(range.start.year, range.start.month, range.start.day, workEndHour);
    var busy = Duration.zero;
    for (final item in _scheduleItems) {
      busy += _overlap(item.startsAt, item.endsAt, workStart, workEnd);
    }
    for (final event in _externalEvents) {
      if (event.allDay) continue;
      busy += _overlap(event.start, event.end, workStart, workEnd);
    }
    final total = workEnd.difference(workStart);
    final remaining = total - busy;
    return remaining.isNegative ? Duration.zero : remaining;
  }

  Duration _overlap(DateTime aStart, DateTime aEnd, DateTime bStart, DateTime bEnd) {
    final start = aStart.isAfter(bStart) ? aStart : bStart;
    final end = aEnd.isBefore(bEnd) ? aEnd : bEnd;
    return end.isAfter(start) ? end.difference(start) : Duration.zero;
  }

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    return Scaffold(
      appBar: AppBar(title: const Text('Today')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(Spacing.lg, Spacing.sm, Spacing.lg, Spacing.xxl),
          children: [
            Text(Format.greeting(now), style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 2),
            Text(
              Format.dayHeading(now),
              style: Theme.of(context)
                  .textTheme
                  .bodyMedium
                  ?.copyWith(color: Theme.of(context).colorScheme.onSurfaceVariant),
            ),
            const SizedBox(height: Spacing.lg),
            if (_status == _LoadStatus.loading) const SkeletonList(count: 4),
            if (_status == _LoadStatus.error)
              ErrorState(message: _error ?? 'Something went wrong.', onRetry: _load),
            if (_status == _LoadStatus.loaded) ..._buildContent(context),
          ],
        ),
      ),
    );
  }

  List<Widget> _buildContent(BuildContext context) {
    final widgets = <Widget>[];

    widgets.add(_FocusTimeCard(available: _focusTimeAvailable));
    widgets.add(const SizedBox(height: Spacing.lg));

    if (_needsAttention.isNotEmpty) {
      widgets.add(_NeedsAttentionCard(items: _needsAttention));
      widgets.add(const SizedBox(height: Spacing.lg));
    }

    if (_highPriority.isNotEmpty) {
      widgets.add(const SectionLabel('High priority'));
      widgets.add(const SizedBox(height: Spacing.sm));
      for (final task in _highPriority) {
        widgets.add(_HighPriorityTile(task: task, scheduleItem: _scheduleFor(task.id)));
        widgets.add(const SizedBox(height: Spacing.sm));
      }
      widgets.add(const SizedBox(height: Spacing.md));
    }

    widgets.add(const SectionLabel('Your day'));
    widgets.add(const SizedBox(height: Spacing.sm));
    final blocks = _dayBlocks();
    if (blocks.isEmpty) {
      widgets.add(const EmptyState(
        icon: Icons.wb_sunny_outlined,
        title: 'Nothing on the calendar yet',
        message: 'Add work and plan your day to fill this in.',
      ));
    } else {
      for (final block in blocks) {
        widgets.add(block);
        widgets.add(const SizedBox(height: Spacing.sm));
      }
    }

    return widgets;
  }

  ScheduleItem? _scheduleFor(String taskId) {
    for (final item in _scheduleItems) {
      if (item.taskId == taskId) return item;
    }
    return null;
  }

  List<Widget> _dayBlocks() {
    final entries = <(DateTime, DateTime, Widget)>[
      for (final item in _scheduleItems)
        (item.startsAt, item.endsAt, _ScheduleBlockTile(item: item)),
      for (final event in _externalEvents)
        (event.start, event.end, _CalendarEventTile(event: event)),
    ]..sort((a, b) => a.$1.compareTo(b.$1));
    return entries.map((e) => e.$3).toList();
  }
}

class _FocusTimeCard extends StatelessWidget {
  const _FocusTimeCard({required this.available});

  final Duration available;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(Spacing.lg),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [scheme.primary, scheme.primary.withValues(alpha: 0.8)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(Corners.lg),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Your day', style: TextStyle(color: scheme.onPrimary.withValues(alpha: 0.85), fontSize: 13)),
          const SizedBox(height: 4),
          Text(
            '${Format.duration(available)} focus time available',
            style: TextStyle(color: scheme.onPrimary, fontSize: 20, fontWeight: FontWeight.w700),
          ),
        ],
      ),
    );
  }
}

class _NeedsAttentionCard extends StatelessWidget {
  const _NeedsAttentionCard({required this.items});

  final List<NeedsAttentionItem> items;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(Spacing.md),
      decoration: BoxDecoration(
        color: const Color(0xFFFEF3C7),
        border: Border.all(color: const Color(0xFFF59E0B)),
        borderRadius: BorderRadius.circular(Corners.md),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Row(
            children: [
              Icon(Icons.warning_amber_rounded, size: 18, color: Color(0xFFB45309)),
              SizedBox(width: 6),
              Text('Needs attention', style: TextStyle(fontWeight: FontWeight.w700, color: Color(0xFFB45309))),
            ],
          ),
          const SizedBox(height: 6),
          for (final item in items)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(
                '${item.title} — calendar event was removed. Re-plan to recreate it.',
                style: const TextStyle(fontSize: 13, color: Color(0xFF92400E)),
              ),
            ),
        ],
      ),
    );
  }
}

class _HighPriorityTile extends StatelessWidget {
  const _HighPriorityTile({required this.task, this.scheduleItem});

  final PrioritizedTask task;
  final ScheduleItem? scheduleItem;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(Corners.lg),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => TaskDetailScreen(taskId: task.id)),
        ),
        child: Padding(
          padding: const EdgeInsets.all(Spacing.md),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(task.title, style: Theme.of(context).textTheme.titleSmall, maxLines: 2, overflow: TextOverflow.ellipsis),
              const SizedBox(height: 6),
              Wrap(
                spacing: 8,
                runSpacing: 4,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  PriorityBadge(score: task.priorityScore),
                  if (task.effortEstimateMinutes != null)
                    Text(Format.durationMinutes(task.effortEstimateMinutes!),
                        style: Theme.of(context).textTheme.bodySmall),
                  if (scheduleItem != null)
                    Text(
                      Format.timeRange(scheduleItem!.startsAt, scheduleItem!.endsAt),
                      style: Theme.of(context)
                          .textTheme
                          .bodySmall
                          ?.copyWith(fontWeight: FontWeight.w600),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ScheduleBlockTile extends StatelessWidget {
  const _ScheduleBlockTile({required this.item});

  final ScheduleItem item;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(Corners.lg),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => TaskDetailScreen(taskId: item.taskId)),
        ),
        child: Padding(
          padding: const EdgeInsets.all(Spacing.md),
          child: Row(
            children: [
              Container(
                width: 4,
                height: 36,
                decoration: BoxDecoration(color: scheme.primary, borderRadius: BorderRadius.circular(2)),
              ),
              const SizedBox(width: Spacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(Format.timeRange(item.startsAt, item.endsAt),
                        style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13)),
                    Text(item.title, maxLines: 1, overflow: TextOverflow.ellipsis),
                  ],
                ),
              ),
              if (item.needsAttention)
                const Icon(Icons.warning_amber_rounded, size: 18, color: Color(0xFFB45309))
              else if (item.googleEventId != null)
                Icon(Icons.check_circle, size: 18, color: scheme.primary),
            ],
          ),
        ),
      ),
    );
  }
}

class _CalendarEventTile extends StatelessWidget {
  const _CalendarEventTile({required this.event});

  final ExternalCalendarEvent event;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(Spacing.md),
        child: Row(
          children: [
            Icon(Icons.event_outlined, size: 18, color: Theme.of(context).colorScheme.onSurfaceVariant),
            const SizedBox(width: Spacing.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    event.allDay ? 'All day' : Format.timeRange(event.start, event.end),
                    style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13),
                  ),
                  Text(event.title ?? '(No title)', maxLines: 1, overflow: TextOverflow.ellipsis),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
