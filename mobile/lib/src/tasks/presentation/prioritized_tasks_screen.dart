import 'package:flutter/material.dart';

import '../../core/calendar_realtime.dart';
import '../../design/format.dart';
import '../../design/tokens.dart';
import '../../design/widgets/empty_state.dart';
import '../../design/widgets/error_state.dart';
import '../../design/widgets/loading_skeleton.dart';
import '../../design/widgets/priority_badge.dart';
import '../data/task_api_client.dart';
import '../data/task_model.dart';
import 'add_work_screen.dart';
import 'task_detail_screen.dart';

enum _LoadStatus { loading, loaded, error }

/// The "Tasks" tab: every open task, prioritized and sorted by AI
/// priority (unprioritized tasks sort last) -- the fuller list Today's
/// "high priority" section only summarizes.
class PrioritizedTasksScreen extends StatefulWidget {
  const PrioritizedTasksScreen({super.key, TaskApiClient? taskApiClient, this.enableRealtime = true})
      : _injectedApi = taskApiClient;

  // Injectable for tests -- defaults to a real [TaskApiClient].
  final TaskApiClient? _injectedApi;
  // Off in widget tests -- see CalendarScreen's `enableRealtime` doc.
  final bool enableRealtime;

  @override
  State<PrioritizedTasksScreen> createState() => _PrioritizedTasksScreenState();
}

class _PrioritizedTasksScreenState extends State<PrioritizedTasksScreen> {
  late final _api = widget._injectedApi ?? TaskApiClient();
  _LoadStatus _status = _LoadStatus.loading;
  List<PrioritizedTask> _tasks = const [];
  String? _error;
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

  Future<void> _load() async {
    if (!mounted) return;
    setState(() => _status = _LoadStatus.loading);
    try {
      final tasks = await _api.listPrioritizedTasks(status: TaskStatus.pending);
      if (!mounted) return;
      setState(() {
        _tasks = tasks;
        _status = _LoadStatus.loaded;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not load tasks. Pull down to retry.';
        _status = _LoadStatus.error;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Tasks')),
      body: switch (_status) {
        _LoadStatus.loading => const Padding(
            padding: EdgeInsets.all(Spacing.lg),
            child: SkeletonList(count: 5),
          ),
        _LoadStatus.error => ErrorState(message: _error ?? 'Something went wrong.', onRetry: _load),
        _LoadStatus.loaded => _tasks.isEmpty
            ? EmptyState(
                icon: Icons.task_alt_outlined,
                title: 'No tasks yet',
                message: 'Add what you need to get done -- AI will help you prioritize it.',
                actionLabel: 'Add work',
                onAction: () => _openAddWork(context),
              )
            : RefreshIndicator(
                onRefresh: _load,
                child: ListView.separated(
                  padding: const EdgeInsets.all(Spacing.lg),
                  itemCount: _tasks.length,
                  separatorBuilder: (_, _) => const SizedBox(height: Spacing.sm),
                  itemBuilder: (context, index) => _TaskCard(
                    task: _tasks[index],
                    onOpen: () async {
                      final changed = await Navigator.of(context).push<bool>(
                        MaterialPageRoute(builder: (_) => TaskDetailScreen(taskId: _tasks[index].id)),
                      );
                      if (changed == true) _load();
                    },
                  ),
                ),
              ),
      },
      floatingActionButton: FloatingActionButton(
        onPressed: () => _openAddWork(context),
        child: const Icon(Icons.add),
      ),
    );
  }

  Future<void> _openAddWork(BuildContext context) async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => const AddWorkScreen()));
    _load();
  }
}

class _TaskCard extends StatelessWidget {
  const _TaskCard({required this.task, required this.onOpen});

  final PrioritizedTask task;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(Corners.lg),
        onTap: onOpen,
        child: Padding(
          padding: const EdgeInsets.all(Spacing.lg),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(task.title, style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: Spacing.sm),
              Wrap(
                spacing: Spacing.sm,
                runSpacing: Spacing.xs,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  PriorityBadge(score: task.priorityScore),
                  if (task.confidenceScore != null)
                    _Meta(icon: Icons.verified_outlined, text: '${(task.confidenceScore! * 100).round()}% confidence'),
                  if (task.dueAt != null)
                    _Meta(icon: Icons.event_outlined, text: 'Due ${_dateOnly(task.dueAt!)}'),
                  if (task.effortEstimateMinutes != null)
                    _Meta(icon: Icons.timer_outlined, text: Format.durationMinutes(task.effortEstimateMinutes!))
                  else if (task.estimatedMinutes != null)
                    _Meta(icon: Icons.timer_outlined, text: Format.durationMinutes(task.estimatedMinutes!)),
                ],
              ),
              if (task.reasoning != null) ...[
                const SizedBox(height: Spacing.sm),
                Text(
                  task.reasoning!,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context)
                      .textTheme
                      .bodySmall
                      ?.copyWith(color: scheme.onSurfaceVariant, fontStyle: FontStyle.italic),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  String _dateOnly(DateTime dt) => dt.toLocal().toString().split(' ').first;
}

class _Meta extends StatelessWidget {
  const _Meta({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.onSurfaceVariant;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 14, color: color),
        const SizedBox(width: 4),
        Text(text, style: TextStyle(fontSize: 12, color: color)),
      ],
    );
  }
}
