import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../calendar/data/calendar_api_client.dart';
import '../../calendar/data/calendar_models.dart';
import '../../design/format.dart';
import '../../design/tokens.dart';
import '../../design/widgets/error_state.dart';
import '../../design/widgets/priority_badge.dart';
import '../../design/widgets/section_label.dart';
import '../data/schedule_api_client.dart';
import '../data/schedule_models.dart';

enum _Status { idle, planning, reviewing, applying, applied, error }
enum _Horizon { day, week }

/// The dedicated planning experience: "Plan my day" / "Plan my week" ->
/// review the proposal -> Apply to Google Calendar -> progress -> success
/// summary. The backend independently revalidates every item before
/// writing anything -- this screen only displays what came back.
class PlanScreen extends StatefulWidget {
  const PlanScreen({super.key, ScheduleApiClient? scheduleApiClient, CalendarApiClient? calendarApiClient})
      : _injectedScheduleApi = scheduleApiClient,
        _injectedCalendarApi = calendarApiClient;

  // Injectable for tests -- default to real clients otherwise.
  final ScheduleApiClient? _injectedScheduleApi;
  final CalendarApiClient? _injectedCalendarApi;

  @override
  State<PlanScreen> createState() => _PlanScreenState();
}

class _PlanScreenState extends State<PlanScreen> {
  late final _scheduleApi = widget._injectedScheduleApi ?? ScheduleApiClient();
  late final _calendarApi = widget._injectedCalendarApi ?? CalendarApiClient();

  _Status _status = _Status.idle;
  _Horizon? _horizon;
  ScheduleProposal? _proposal;
  CalendarConnection? _connection;
  ScheduleApplyResult? _applyResult;
  String? _error;
  String? _errorCode;

  @override
  void initState() {
    super.initState();
    _calendarApi.getConnection().then((c) {
      if (mounted) setState(() => _connection = c);
    }).catchError((_) {});
  }

  ({DateTime start, DateTime end}) _range(_Horizon horizon) {
    final now = DateTime.now();
    final start = DateTime(now.year, now.month, now.day, now.hour);
    return horizon == _Horizon.day
        ? (start: start, end: DateTime(now.year, now.month, now.day + 1))
        : (start: start, end: start.add(const Duration(days: 7)));
  }

  Future<void> _plan(_Horizon horizon) async {
    setState(() {
      _status = _Status.planning;
      _horizon = horizon;
      _error = null;
      _applyResult = null;
    });
    try {
      final range = _range(horizon);
      final proposal = await _scheduleApi.proposeSchedule(horizonStart: range.start, horizonEnd: range.end);
      setState(() {
        _proposal = proposal;
        _status = _Status.reviewing;
      });
    } catch (e) {
      _handleError(e);
    }
  }

  Future<void> _connectWriteAccess() async {
    try {
      final url = await _calendarApi.getWriteScopeConnectUrl();
      await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
    } catch (e) {
      _handleError(e);
    }
  }

  Future<void> _apply() async {
    final proposal = _proposal;
    if (proposal == null || proposal.scheduled.isEmpty) return;
    setState(() {
      _status = _Status.applying;
      _error = null;
    });
    try {
      final result = await _scheduleApi.applySchedule(proposal.scheduled);
      setState(() {
        _applyResult = result;
        _status = _Status.applied;
      });
    } catch (e) {
      _handleError(e);
    }
  }

  void _handleError(Object e) {
    if (e is ScheduleApiException) {
      _error = e.isReauthRequired
          ? 'Your Google Calendar connection needs to be reconnected.'
          : e.message;
      _errorCode = e.code;
    } else {
      _error = 'Something went wrong. Please try again.';
      _errorCode = null;
    }
    setState(() => _status = _Status.error);
  }

  void _reset() {
    setState(() {
      _status = _Status.idle;
      _horizon = null;
      _proposal = null;
      _applyResult = null;
      _error = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final busy = _status == _Status.planning || _status == _Status.applying;

    return Scaffold(
      appBar: AppBar(title: const Text('Plan')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(Spacing.lg),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (_status == _Status.idle || _status == _Status.error) ...[
                Text('Build your plan', style: Theme.of(context).textTheme.headlineSmall),
                const SizedBox(height: Spacing.xs),
                Text(
                  'AI combines your prioritized tasks with real Google Calendar availability.',
                  style: Theme.of(context)
                      .textTheme
                      .bodyMedium
                      ?.copyWith(color: Theme.of(context).colorScheme.onSurfaceVariant),
                ),
                const SizedBox(height: Spacing.xl),
                if (_status == _Status.error) ...[
                  ErrorState(
                    message: _errorCode == 'CALENDAR_WRITE_SCOPE_REQUIRED'
                        ? 'Calendar write permission is required. Connect Calendar permissions, then try again.'
                        : (_error ?? 'Something went wrong.'),
                  ),
                  const SizedBox(height: Spacing.lg),
                ],
                Row(
                  children: [
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: busy ? null : () => _plan(_Horizon.day),
                        icon: const Icon(Icons.today_outlined),
                        label: const Text('Plan my day'),
                      ),
                    ),
                    const SizedBox(width: Spacing.md),
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: busy ? null : () => _plan(_Horizon.week),
                        icon: const Icon(Icons.view_week_outlined),
                        label: const Text('Plan my week'),
                      ),
                    ),
                  ],
                ),
              ],
              if (_status == _Status.planning) ...[
                const SizedBox(height: Spacing.xxl * 2),
                const Center(child: CircularProgressIndicator()),
                const SizedBox(height: Spacing.md),
                const Center(child: Text('Loading availability and building your plan…')),
              ],
              if ((_status == _Status.reviewing || _status == _Status.applying) && _proposal != null)
                _ReviewSection(
                  proposal: _proposal!,
                  horizon: _horizon ?? _Horizon.day,
                  connection: _connection,
                  busy: busy,
                  onApply: _apply,
                  onConnectWriteAccess: _connectWriteAccess,
                ),
              if (_status == _Status.applied && _applyResult != null)
                _AppliedSummary(
                  result: _applyResult!,
                  titleByTaskId: {for (final i in _proposal!.scheduled) i.taskId: i.title},
                  onDone: _reset,
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ReviewSection extends StatelessWidget {
  const _ReviewSection({
    required this.proposal,
    required this.horizon,
    required this.connection,
    required this.busy,
    required this.onApply,
    required this.onConnectWriteAccess,
  });

  final ScheduleProposal proposal;
  final _Horizon horizon;
  final CalendarConnection? connection;
  final bool busy;
  final VoidCallback onApply;
  final VoidCallback onConnectWriteAccess;

  @override
  Widget build(BuildContext context) {
    if (proposal.scheduled.isEmpty && proposal.unscheduled.isEmpty) {
      return Padding(
        padding: const EdgeInsets.only(top: Spacing.xxl),
        child: Column(
          children: [
            Icon(Icons.inbox_outlined, size: 40, color: Theme.of(context).colorScheme.outline),
            const SizedBox(height: Spacing.md),
            const Text(
              'No unscheduled, prioritized tasks found. Add work and prioritize it with AI first.',
              textAlign: TextAlign.center,
            ),
          ],
        ),
      );
    }

    final grouped = <String, List<ProposedScheduleItem>>{};
    for (final item in proposal.scheduled) {
      final key = Format.dayHeading(item.start.toLocal());
      grouped.putIfAbsent(key, () => []).add(item);
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SizedBox(height: Spacing.lg),
        const SectionLabel('Your plan'),
        const SizedBox(height: Spacing.sm),
        for (final entry in grouped.entries) ...[
          if (horizon == _Horizon.week) ...[
            Text(entry.key, style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: Spacing.xs),
          ],
          for (final item in entry.value) ...[
            _PlanItemTile(item: item),
            const SizedBox(height: Spacing.sm),
          ],
          const SizedBox(height: Spacing.sm),
        ],
        if (proposal.unscheduled.isNotEmpty) ...[
          const SectionLabel('Could not schedule'),
          const SizedBox(height: Spacing.sm),
          for (final item in proposal.unscheduled)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child: Text.rich(
                TextSpan(
                  children: [
                    TextSpan(
                      text: '${item.title.isEmpty ? "(task)" : item.title}: ',
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    TextSpan(text: item.reason),
                  ],
                ),
                style: TextStyle(color: Theme.of(context).colorScheme.tertiary, fontSize: 13),
              ),
            ),
        ],
        if (proposal.scheduled.isNotEmpty) ...[
          const SizedBox(height: Spacing.lg),
          if (connection?.hasWriteAccess == true)
            FilledButton.icon(
              onPressed: busy ? null : onApply,
              icon: const Icon(Icons.cloud_upload_outlined),
              label: Text(busy ? 'Applying…' : 'Apply to Google Calendar'),
            )
          else
            OutlinedButton.icon(
              onPressed: onConnectWriteAccess,
              icon: const Icon(Icons.link),
              label: const Text('Connect Calendar permissions'),
            ),
        ],
      ],
    );
  }
}

class _PlanItemTile extends StatelessWidget {
  const _PlanItemTile({required this.item});

  final ProposedScheduleItem item;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(Spacing.md),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 4,
              height: 44,
              decoration: BoxDecoration(color: scheme.primary, borderRadius: BorderRadius.circular(2)),
            ),
            const SizedBox(width: Spacing.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(Format.timeRange(item.start, item.end),
                      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13)),
                  Text(item.title, style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 4),
                  PriorityBadge(score: item.priorityScore),
                  const SizedBox(height: 4),
                  Text(
                    item.reason,
                    style: Theme.of(context)
                        .textTheme
                        .bodySmall
                        ?.copyWith(fontStyle: FontStyle.italic, color: scheme.onSurfaceVariant),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _AppliedSummary extends StatelessWidget {
  const _AppliedSummary({required this.result, required this.titleByTaskId, required this.onDone});

  final ScheduleApplyResult result;
  final Map<String, String> titleByTaskId;
  final VoidCallback onDone;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SizedBox(height: Spacing.xl),
        Icon(Icons.check_circle, size: 48, color: Theme.of(context).colorScheme.primary),
        const SizedBox(height: Spacing.sm),
        Text(
          '${result.created} task${result.created == 1 ? '' : 's'} scheduled',
          style: Theme.of(context).textTheme.titleLarge,
          textAlign: TextAlign.center,
        ),
        if (result.alreadyApplied > 0 || result.failed > 0)
          Text(
            [
              if (result.alreadyApplied > 0) '${result.alreadyApplied} already applied',
              if (result.failed > 0) '${result.failed} failed',
            ].join(' · '),
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.bodyMedium,
          ),
        const SizedBox(height: Spacing.lg),
        for (final r in result.results)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Row(
              children: [
                Icon(
                  r.status == AppliedItemStatus.failed ? Icons.close : Icons.check,
                  size: 18,
                  color: r.status == AppliedItemStatus.failed
                      ? Theme.of(context).colorScheme.error
                      : const Color(0xFF16A34A),
                ),
                const SizedBox(width: Spacing.sm),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(titleByTaskId[r.taskId] ?? r.taskId),
                      if (r.start != null && r.end != null)
                        Text(Format.timeRange(r.start!, r.end!), style: Theme.of(context).textTheme.bodySmall),
                      if (r.status == AppliedItemStatus.failed && r.reason != null)
                        Text(r.reason!, style: TextStyle(color: Theme.of(context).colorScheme.error, fontSize: 12)),
                    ],
                  ),
                ),
              ],
            ),
          ),
        const SizedBox(height: Spacing.lg),
        OutlinedButton(onPressed: onDone, child: const Text('Done')),
      ],
    );
  }
}
