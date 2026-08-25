import 'package:flutter/material.dart';

import '../../design/format.dart';
import '../../design/tokens.dart';
import '../../design/widgets/error_state.dart';
import '../../scheduling/data/schedule_api_client.dart';
import '../../scheduling/data/schedule_models.dart';
import '../data/task_api_client.dart';
import '../data/task_model.dart';
import 'ai_prioritization_section.dart';
import 'task_form_screen.dart';

enum _LoadStatus { loading, loaded, error }

/// Full detail view for one task: everything the Today/Tasks lists only
/// summarize, plus Edit / Complete / Reschedule / Delete actions.
class TaskDetailScreen extends StatefulWidget {
  const TaskDetailScreen({
    super.key,
    required this.taskId,
    TaskApiClient? taskApiClient,
    ScheduleApiClient? scheduleApiClient,
  })  : _injectedTaskApi = taskApiClient,
        _injectedScheduleApi = scheduleApiClient;

  final String taskId;
  // Injectable for tests -- default to real clients otherwise.
  final TaskApiClient? _injectedTaskApi;
  final ScheduleApiClient? _injectedScheduleApi;

  @override
  State<TaskDetailScreen> createState() => _TaskDetailScreenState();
}

class _TaskDetailScreenState extends State<TaskDetailScreen> {
  late final _taskApi = widget._injectedTaskApi ?? TaskApiClient();
  late final _scheduleApi = widget._injectedScheduleApi ?? ScheduleApiClient();

  _LoadStatus _status = _LoadStatus.loading;
  String? _error;
  Task? _task;
  ScheduleItem? _scheduleItem;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _status = _LoadStatus.loading);
    try {
      final now = DateTime.now();
      final results = await Future.wait([
        _taskApi.getTask(widget.taskId),
        _scheduleApi.listScheduleItems(
          now.subtract(const Duration(days: 365)),
          now.add(const Duration(days: 365)),
          taskId: widget.taskId,
        ),
      ]);
      if (!mounted) return;
      final scheduleItems = results[1] as List<ScheduleItem>;
      setState(() {
        _task = results[0] as Task;
        _scheduleItem = scheduleItems.isEmpty ? null : scheduleItems.first;
        _status = _LoadStatus.loaded;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not load this task.';
        _status = _LoadStatus.error;
      });
    }
  }

  Future<void> _complete() async {
    setState(() => _busy = true);
    try {
      await _taskApi.completeTask(widget.taskId);
      if (mounted) Navigator.of(context).pop(true);
    } on TaskApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _delete() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete this task?'),
        content: const Text('This cannot be undone.'),
        actions: [
          TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.of(context).pop(true), child: const Text('Delete')),
        ],
      ),
    );
    if (confirmed != true) return;
    setState(() => _busy = true);
    try {
      await _taskApi.deleteTask(widget.taskId);
      if (mounted) Navigator.of(context).pop(true);
    } on TaskApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _reschedule() async {
    final task = _task;
    if (task == null) return;
    final date = await showDatePicker(
      context: context,
      initialDate: DateTime.now(),
      firstDate: DateTime.now().subtract(const Duration(days: 1)),
      lastDate: DateTime.now().add(const Duration(days: 365)),
    );
    if (date == null || !mounted) return;
    final time = await showTimePicker(context: context, initialTime: TimeOfDay.now());
    if (time == null || !mounted) return;

    final start = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    final minutes = task.estimatedMinutes ?? _scheduleItem?.duration.inMinutes ?? 60;
    final end = start.add(Duration(minutes: minutes));

    setState(() => _busy = true);
    try {
      final result = await _scheduleApi.applySingleItem(widget.taskId, start, end);
      final item = result.results.first;
      if (!mounted) return;
      if (item.status == AppliedItemStatus.failed) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(item.reason ?? 'Could not reschedule.')));
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Rescheduled to ${Format.timeRange(start, end)}.')),
        );
        await _load();
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('Could not reschedule. Try again.')));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = _task;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Task'),
        actions: [
          if (task != null)
            IconButton(
              icon: const Icon(Icons.edit_outlined),
              tooltip: 'Edit',
              onPressed: _busy
                  ? null
                  : () async {
                      final changed = await Navigator.of(context).push<bool>(
                        MaterialPageRoute(builder: (_) => TaskFormScreen(task: task)),
                      );
                      if (changed == true) _load();
                    },
            ),
        ],
      ),
      body: switch (_status) {
        _LoadStatus.loading => const Center(child: CircularProgressIndicator()),
        _LoadStatus.error => ErrorState(message: _error ?? 'Something went wrong.', onRetry: _load),
        _LoadStatus.loaded => _buildBody(context, task!),
      },
    );
  }

  Widget _buildBody(BuildContext context, Task task) {
    final scheme = Theme.of(context).colorScheme;
    final isDone = task.status == TaskStatus.done;

    return ListView(
      padding: const EdgeInsets.fromLTRB(Spacing.lg, Spacing.md, Spacing.lg, Spacing.xxl),
      children: [
        Text(
          task.title,
          style: Theme.of(context)
              .textTheme
              .headlineSmall
              ?.copyWith(decoration: isDone ? TextDecoration.lineThrough : null),
        ),
        if (task.description != null && task.description!.isNotEmpty) ...[
          const SizedBox(height: Spacing.sm),
          Text(task.description!, style: Theme.of(context).textTheme.bodyLarge),
        ],
        const SizedBox(height: Spacing.lg),
        Wrap(
          spacing: Spacing.sm,
          runSpacing: Spacing.sm,
          children: [
            if (task.dueAt != null) _InfoChip(icon: Icons.event_outlined, label: 'Due ${_dateOnly(task.dueAt!)}'),
            if (task.estimatedMinutes != null)
              _InfoChip(icon: Icons.timer_outlined, label: Format.durationMinutes(task.estimatedMinutes!)),
            _InfoChip(icon: Icons.flag_outlined, label: task.status.label),
          ],
        ),
        const SizedBox(height: Spacing.xl),
        if (_scheduleItem != null) ...[
          Card(
            child: Padding(
              padding: const EdgeInsets.all(Spacing.lg),
              child: Row(
                children: [
                  Icon(Icons.event_available_outlined, color: scheme.primary),
                  const SizedBox(width: Spacing.md),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Scheduled', style: Theme.of(context).textTheme.titleSmall),
                        Text(
                          '${_dateOnly(_scheduleItem!.startsAt)} · ${Format.timeRange(_scheduleItem!.startsAt, _scheduleItem!.endsAt)}',
                        ),
                        Text(
                          _calendarStatusLabel(_scheduleItem!),
                          style: TextStyle(
                            color: _scheduleItem!.needsAttention ? const Color(0xFFB45309) : scheme.primary,
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: Spacing.lg),
        ],
        AiPrioritizationSection(taskId: task.id, apiClient: _taskApi),
        const SizedBox(height: Spacing.xl),
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: _busy ? null : _reschedule,
                icon: const Icon(Icons.schedule_outlined),
                label: const Text('Reschedule'),
              ),
            ),
            const SizedBox(width: Spacing.sm),
            Expanded(
              child: FilledButton.icon(
                onPressed: _busy || isDone ? null : _complete,
                icon: const Icon(Icons.check),
                label: Text(isDone ? 'Completed' : 'Complete'),
              ),
            ),
          ],
        ),
        const SizedBox(height: Spacing.sm),
        OutlinedButton.icon(
          onPressed: _busy ? null : _delete,
          style: OutlinedButton.styleFrom(foregroundColor: scheme.error, side: BorderSide(color: scheme.error)),
          icon: const Icon(Icons.delete_outline),
          label: const Text('Delete task'),
        ),
      ],
    );
  }

  String _calendarStatusLabel(ScheduleItem item) {
    if (item.needsAttention) return 'Removed from Google Calendar -- reschedule to recreate it';
    if (item.syncStatus == 'synced') return 'On Google Calendar';
    return 'Not yet applied to Google Calendar';
  }

  String _dateOnly(DateTime dt) => dt.toLocal().toString().split(' ').first;
}

class _InfoChip extends StatelessWidget {
  const _InfoChip({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Chip(
      avatar: Icon(icon, size: 16),
      label: Text(label),
      visualDensity: VisualDensity.compact,
    );
  }
}
